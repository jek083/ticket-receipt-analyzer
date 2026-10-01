import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, Sequence

import pdfplumber
import pytesseract
from PIL import Image, ImageFilter, ImageOps
from pypdf import PdfReader
from pypdf.errors import PdfReadError

from src.image_prep import crop_to_receipt, flatten_transparency, normalize_background

logger = logging.getLogger(__name__)

METHODE_TEXTE = "TEXTE"
METHODE_OCR = "OCR"

PDF_EXTENSIONS = frozenset({".pdf"})
IMAGE_EXTENSIONS = frozenset({".png", ".jpg", ".jpeg"})
SUPPORTED_EXTENSIONS = PDF_EXTENSIONS | IMAGE_EXTENSIONS

IMAGE_OCR_CONFIG = "--psm 6"


@dataclass(frozen=True)
class ExtractionResult:
    """Extracted text and how it was obtained (native text or OCR)."""

    text: str
    methode: str


class PDFExtractor:
    def __init__(
        self,
        ocr_enabled=True,
        ocr_language='fra+eng',
        ocr_resolution=300,
        image_min_width=0,
        image_max_width=0,
        crop_to_ticket=False,
        ocr_retry_scales: Sequence[float] = (),
        ocr_thicken_scales: Sequence[float] = (),
    ):
        self.ocr_enabled = ocr_enabled
        self.ocr_language = ocr_language
        self.ocr_resolution = ocr_resolution
        self.image_min_width = image_min_width
        self.image_max_width = image_max_width
        self.crop_to_ticket = crop_to_ticket
        # Image widths (relative to the first pass) to try when a receipt reads badly.
        self.ocr_retry_scales = tuple(ocr_retry_scales)
        # Last resort for faint thermal prints: the same scales with the dark strokes thickened.
        self.ocr_thicken_scales = tuple(ocr_thicken_scales)
        self.failed_files: Dict[str, str] = {}

    def extract_from_file(self, path):
        """Return the text of a PDF or image file."""
        return self.extract(path).text

    def extract(self, path) -> ExtractionResult:
        """Extract the text of a PDF or image file and report the method used."""
        path = Path(path)

        if not path.exists():
            raise FileNotFoundError(f"Fichier non trouve: {path}")

        suffix = path.suffix.lower()
        if suffix in PDF_EXTENSIONS:
            text, used_ocr = self._extract_pdf(path)
        elif suffix in IMAGE_EXTENSIONS:
            text, used_ocr = self._extract_image(path), True
        else:
            raise ValueError(f"Format de fichier non supporte: {path.name}")

        return ExtractionResult(text, METHODE_OCR if used_ocr else METHODE_TEXTE)

    def extract_alternatives(self, path) -> Iterator[ExtractionResult]:
        """Yield further OCR readings of an image at other scales, lazily.

        Tesseract reads the same receipt differently depending on its size: the
        caller decides whether another reading is worth trying.
        """
        path = Path(path)
        if path.suffix.lower() not in IMAGE_EXTENSIONS or not self.ocr_enabled:
            return
        passes = [(scale, False) for scale in self.ocr_retry_scales]
        passes += [(scale, True) for scale in self.ocr_thicken_scales]
        for scale, thicken in passes:
            try:
                text = self._extract_image(path, scale, thicken)
            except (ValueError, pytesseract.TesseractError):
                continue
            yield ExtractionResult(text, METHODE_OCR)

    def _extract_pdf(self, pdf_path):
        logger.info(f"Extraction du PDF: {pdf_path.name}")

        page_texts = []
        used_ocr = False
        pypdf_reader = None
        pypdf_unavailable = False
        with pdfplumber.open(pdf_path) as pdf:
            if not pdf.pages:
                raise ValueError(f"Le PDF ne contient aucune page: {pdf_path.name}")

            for page_num, page in enumerate(pdf.pages, start=1):
                page_text = page.extract_text()

                if not page_text or not page_text.strip():
                    if not pypdf_unavailable:
                        try:
                            if pypdf_reader is None:
                                pypdf_reader = PdfReader(
                                    str(pdf_path),
                                    strict=False,
                                )

                            if page_num <= len(pypdf_reader.pages):
                                pypdf_text = (
                                    pypdf_reader.pages[page_num - 1].extract_text(
                                        extraction_mode="layout"
                                    )
                                    or ""
                                )
                                if pypdf_text.strip():
                                    page_text = pypdf_text
                                    logger.info(
                                        "Texte natif extrait avec pypdf page %s",
                                        page_num,
                                    )
                        except PdfReadError as error:
                            logger.warning(
                                "Impossible d'extraire le texte avec pypdf: %s",
                                error,
                            )
                            pypdf_unavailable = True

                if not page_text or not page_text.strip():
                    if not self.ocr_enabled:
                        raise ValueError(
                            f"Aucun texte natif page {page_num} "
                            "(pdfplumber et pypdf) et OCR desactive"
                        )
                    logger.info(
                        f"Pas de texte natif page {page_num}, utilisation OCR"
                    )
                    page_text = self._ocr_page(page)
                    used_ocr = True

                if not page_text or not page_text.strip():
                    logger.warning(
                        "Aucun texte extrait page %s; page ignoree",
                        page_num,
                    )
                    continue

                page_texts.append(page_text.strip())

        if not page_texts:
            raise ValueError("Aucun texte extrait du PDF")

        return "\n".join(page_texts), used_ocr

    def _extract_image(self, image_path, scale=1.0, thicken=False):
        logger.info(f"Extraction de l'image: {image_path.name}")
        if not self.ocr_enabled:
            raise ValueError(
                f"OCR desactive: impossible de lire l'image {image_path.name}"
            )

        with Image.open(image_path) as source:
            image = self._prepare_image(source, scale, thicken)

        try:
            text = pytesseract.image_to_string(
                image,
                lang=self.ocr_language,
                config=IMAGE_OCR_CONFIG,
            )
        except pytesseract.TesseractNotFoundError:
            logger.error(
                "Tesseract OCR non installe. Installer: "
                "https://github.com/UB-Mannheim/tesseract/wiki"
            )
            raise
        except pytesseract.TesseractError as error:
            logger.error(f"Erreur Tesseract OCR: {error}")
            raise

        if not text.strip():
            raise ValueError(f"Aucun texte extrait de l'image {image_path.name}")
        return text.strip()

    def _prepare_image(self, image, scale=1.0, thicken=False):
        """Orient, grayscale, boost contrast, crop a flatbed scan and resize."""
        prepared = ImageOps.exif_transpose(image)
        if self.crop_to_ticket:
            prepared = flatten_transparency(prepared)
        prepared = ImageOps.autocontrast(prepared.convert("L"), cutoff=1)
        if self.crop_to_ticket:
            cropped = crop_to_receipt(normalize_background(prepared))
            if cropped is not None:
                prepared = cropped
        target_width = prepared.width
        if 0 < prepared.width < self.image_min_width:
            target_width = self.image_min_width
        elif self.image_max_width and prepared.width > self.image_max_width:
            # Tesseract reads oversized photos worse than downscaled ones.
            target_width = self.image_max_width
        target_width = round(target_width * scale)
        if target_width != prepared.width:
            prepared = prepared.resize(
                (target_width, round(prepared.height * target_width / prepared.width)),
                Image.LANCZOS,
            )
        if thicken:
            prepared = prepared.filter(ImageFilter.MinFilter(3))
        return prepared

    def _ocr_page(self, page):
        try:
            image = page.to_image(resolution=self.ocr_resolution)
            return pytesseract.image_to_string(
                image.original,
                lang=self.ocr_language,
                config='--psm 6'
            )
        except pytesseract.TesseractNotFoundError:
            logger.error(
                "Tesseract OCR non installe. Installer: "
                "https://github.com/UB-Mannheim/tesseract/wiki"
            )
            raise
        except pytesseract.TesseractError as error:
            logger.error(f"Erreur Tesseract OCR: {error}")
            raise

    def extract_from_folder(self, folder_path):
        folder_path = Path(folder_path)
        results = {}
        self.failed_files = {}

        for pdf_file in folder_path.glob("*.pdf"):
            try:
                text = self.extract_from_file(pdf_file)
                results[pdf_file.name] = text
            except Exception as e:
                self.failed_files[pdf_file.name] = str(e)
                logger.error(f"Impossible de traiter {pdf_file.name}: {e}")

        logger.info(
            f"Extraction terminee: {len(results)} PDF(s) traites, "
            f"{len(self.failed_files)} en echec"
        )
        return results