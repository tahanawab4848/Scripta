import os
from typing import Dict, Any, List, Tuple
import cv2
import numpy as np
from PIL import Image
from config import AppConfig


class PagePreprocessor:
    """
    OpenCV preprocessing pipeline tailored for multilingual (Urdu/Arabic/Persian/English)
    book scans. Preserves delicate diacritics and nuqtas while cleaning background noise,
    spine curvature shadows, page curl, and bleed-through artifacts.
    """

    def __init__(self, config: AppConfig):
        self.config = config

    def process_page(
        self, pil_image: Image.Image, page_number: int
    ) -> Tuple[Image.Image, Dict[str, Any]]:
        """
        Run the configured OpenCV preprocessing pipeline on a single rendered page.
        Returns the processed PIL image and metadata (skew angle, extracted illustrations).
        """
        rgb_np = np.array(pil_image.convert("RGB"))
        bgr_np = cv2.cvtColor(rgb_np, cv2.COLOR_RGB2BGR)
        gray = cv2.cvtColor(bgr_np, cv2.COLOR_BGR2GRAY)

        meta: Dict[str, Any] = {
            "page_number": page_number,
            "width": int(gray.shape[1]),
            "height": int(gray.shape[0]),
            "skew_angle": 0.0,
            "illustrations": [],
        }

        if self.config.enable_border_removal:
            gray, bgr_np = self.remove_borders(gray, bgr_np)

        if self.config.enable_deskew:
            gray, bgr_np, angle = self.deskew(gray, bgr_np)
            meta["skew_angle"] = round(float(angle), 3)

        if self.config.enable_polynomial_dewarping:
            gray, bgr_np = self.dewarp_page_curl(gray, bgr_np)

        if self.config.enable_spine_shadow_removal:
            gray = self.remove_spine_shadow(gray)

        if self.config.enable_bleed_through_reduction:
            gray = self.reduce_bleed_through(gray)

        if getattr(self.config, "enable_ghost_dot_removal", True):
            gray = self.remove_ghost_dots(gray)


        if self.config.extract_embedded_images:
            illustrations = self.extract_illustrations(bgr_np, gray, page_number)
            meta["illustrations"] = illustrations

        if self.config.enable_contrast_normalization:
            gray = self.normalize_contrast(gray)

        if self.config.enable_denoise:
            gray = self.denoise(gray)

        if self.config.enable_adaptive_threshold:
            gray = self.adaptive_threshold(gray)

        processed_pil = Image.fromarray(gray)
        return processed_pil, meta

    def remove_borders(
        self, gray: np.ndarray, bgr: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Mask out dark scanner borders around the page perimeter and flood-fill edge artifacts.
        """
        h, w = gray.shape
        margin = max(4, min(self.config.border_margin_px, min(h, w) // 20))

        cleaned_gray = gray.copy()
        cleaned_bgr = bgr.copy()

        # Estimate paper background intensity from interior median
        interior = cleaned_gray[h // 10 : 9 * h // 10, w // 10 : 9 * w // 10]
        bg_val = int(np.median(interior)) if interior.size else 250
        bg_val = max(230, min(255, bg_val))

        # Blank outer scanner rim
        cleaned_gray[:margin, :] = bg_val
        cleaned_gray[h - margin :, :] = bg_val
        cleaned_gray[:, :margin] = bg_val
        cleaned_gray[:, w - margin :] = bg_val

        cleaned_bgr[:margin, :] = (bg_val, bg_val, bg_val)
        cleaned_bgr[h - margin :, :] = (bg_val, bg_val, bg_val)
        cleaned_bgr[:, :margin] = (bg_val, bg_val, bg_val)
        cleaned_bgr[:, w - margin :] = (bg_val, bg_val, bg_val)

        return cleaned_gray, cleaned_bgr

    def deskew(
        self, gray: np.ndarray, bgr: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Detect text line orientation via morphological horizontal dilation and Hough/minAreaRect,
        then rotate both grayscale and color images cleanly.
        """
        h, w = gray.shape
        inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

        # Connect characters into horizontal text lines without merging adjacent lines
        kernel_w = max(25, w // 40)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kernel_w, 3))
        dilated = cv2.dilate(inv, kernel, iterations=1)

        contours, _ = cv2.findContours(
            dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE
        )

        angles: List[float] = []
        min_line_w = w * 0.12
        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            if cw < min_line_w or ch > h * 0.15 or cw < ch * 2.5:
                continue
            rect = cv2.minAreaRect(cnt)
            angle = rect[-1]
            if angle < -45:
                angle = 90 + angle
            elif angle > 45:
                angle = angle - 90
            if abs(angle) <= self.config.max_skew_angle:
                angles.append(angle)

        if not angles:
            return gray, bgr, 0.0

        median_angle = float(np.median(angles))
        if abs(median_angle) < 0.15:
            return gray, bgr, 0.0

        center = (w // 2, h // 2)
        rot_mat = cv2.getRotationMatrix2D(center, median_angle, 1.0)

        rot_gray = cv2.warpAffine(
            gray,
            rot_mat,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        rot_bgr = cv2.warpAffine(
            bgr,
            rot_mat,
            (w, h),
            flags=cv2.INTER_CUBIC,
            borderMode=cv2.BORDER_REPLICATE,
        )
        return rot_gray, rot_bgr, median_angle

    def dewarp_page_curl(
        self, gray: np.ndarray, bgr: np.ndarray
    ) -> Tuple[np.ndarray, np.ndarray]:
        """
        Straighten 3D non-linear page curl along the book spine by fitting polynomial
        displacement curves across horizontal line baselines and applying a vertical remap mesh.
        """
        h, w = gray.shape
        inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]

        # Connect text into continuous horizontal ribbons
        kw = max(35, w // 30)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, 3))
        dilated = cv2.dilate(inv, kernel, iterations=1)

        contours, _ = cv2.findContours(dilated, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        long_contours = [
            cnt for cnt in contours if cv2.boundingRect(cnt)[2] > w * 0.25
        ]

        if len(long_contours) < 3:
            return gray, bgr

        # Sample contour points across x-coordinates to model the curvature
        x_pts: List[float] = []
        y_shifts: List[float] = []

        for cnt in long_contours:
            pts = cnt.reshape(-1, 2)
            pts = pts[pts[:, 0].argsort()]
            if len(pts) < 10:
                continue
            # Reference height at median X of this line
            med_idx = len(pts) // 2
            ref_y = float(pts[med_idx, 1])
            for pt in pts[:: max(1, len(pts) // 15)]:
                x_pts.append(float(pt[0]))
                y_shifts.append(float(pt[1]) - ref_y)

        if len(x_pts) < 30:
            return gray, bgr

        # Fit a 2nd degree polynomial curve: delta_y = a * x^2 + b * x + c
        try:
            poly = np.polyfit(x_pts, y_shifts, deg=2)
            curve_fn = np.poly1d(poly)

            x_range = np.arange(w, dtype=np.float32)
            y_curve = curve_fn(x_range)
            max_deviation = float(np.max(np.abs(y_curve)))

            # Only apply if curvature is noticeable (> 8px) and physically realistic (< 12% of height)
            if 8.0 < max_deviation < (h * 0.12):
                map_x, map_y = np.meshgrid(
                    np.arange(w, dtype=np.float32), np.arange(h, dtype=np.float32)
                )
                # Apply inverse vertical shift to straighten curved text lines
                map_y = map_y + y_curve.reshape(1, w)

                dewarped_gray = cv2.remap(
                    gray, map_x, map_y, interpolation=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
                )
                dewarped_bgr = cv2.remap(
                    bgr, map_x, map_y, interpolation=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
                )
                return dewarped_gray, dewarped_bgr
        except Exception:
            pass

        return gray, bgr

    def remove_spine_shadow(self, gray: np.ndarray) -> np.ndarray:
        """
        Flatten non-uniform illumination and dark spine/gutter shadows by estimating
        background illumination surface with large-kernel morphological closing, then dividing.
        """
        h, w = gray.shape
        # Kernel width scaled to cover gutter gradient without wiping text blocks
        kw = max(31, (w // 20) | 1)
        kh = max(21, (h // 30) | 1)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (kw, kh))

        # Background surface estimation
        bg = cv2.morphologyEx(gray, cv2.MORPH_CLOSE, kernel)
        bg = cv2.GaussianBlur(bg, (21, 21), 0)

        # Normalize via background division
        normalized = cv2.divide(gray, bg, scale=255)
        return normalized

    def reduce_bleed_through(self, gray: np.ndarray) -> np.ndarray:
        """
        Suppress faint ink bleed-through from the reverse side of thin book paper.
        Foreground ink is strong and dense, whereas bleed-through is faint and low-contrast.
        """
        # Estimate median background brightness
        h, w = gray.shape
        sample = gray[h // 8 : 7 * h // 8, w // 8 : 7 * w // 8]
        if sample.size == 0:
            return gray

        bg_level = float(np.median(sample))
        # Bleed-through typically sits between 75% and 92% of background brightness
        lower_bound = int(bg_level * 0.76)
        upper_bound = int(bg_level * 0.96)

        cleaned = gray.copy()
        # Suppress faint stroke regions closer to background level
        bleed_mask = (cleaned > lower_bound) & (cleaned < upper_bound)
        # Push bleed-through areas closer to clean paper white
        cleaned[bleed_mask] = np.clip(
            cleaned[bleed_mask].astype(np.float32) * 1.08, 0, 255
        ).astype(np.uint8)

        return cleaned

    def remove_ghost_dots(self, gray: np.ndarray) -> np.ndarray:
        """
        Filter out isolated ghost ink flecks and reverse-page bleed dots on thin onion-skin paper
        that Tesseract frequently confuses for authentic Arabic/Urdu diacritics or punctuation.
        """
        if not getattr(self.config, "enable_ghost_dot_removal", True):
            return gray

        # Binarize inverted to isolate tiny speckles
        inv = (gray < 165).astype(np.uint8) * 255
        num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(inv, connectivity=8)

        cleaned = gray.copy()
        for i in range(1, num_labels):
            area = stats[i, cv2.CC_STAT_AREA]
            w = stats[i, cv2.CC_STAT_WIDTH]
            h = stats[i, cv2.CC_STAT_HEIGHT]

            # Ghost bleed-through dots are typically tiny isolated specs (< 4x4 px, area <= 8)
            # with low darkness compared to authentic ink
            if area <= 8 and w <= 3 and h <= 3:
                mask = (labels == i)
                # If the dot is faint (mean intensity > 105), wipe it to paper white
                if float(np.mean(gray[mask])) > 105.0:
                    cleaned[mask] = 255

        return cleaned


    def normalize_contrast(self, gray: np.ndarray) -> np.ndarray:
        """
        Apply Contrast Limited Adaptive Histogram Equalization (CLAHE) to even out
        page lighting.
        """
        clahe = cv2.createCLAHE(
            clipLimit=max(1.0, float(self.config.clahe_clip_limit)),
            tileGridSize=(8, 8),
        )
        enhanced = clahe.apply(gray)
        return enhanced

    def denoise(self, gray: np.ndarray) -> np.ndarray:
        """
        Edge-preserving non-local means denoising tuned so small Urdu/Arabic dots
        and harakat are not wiped out.
        """
        h_strength = max(3, min(15, int(self.config.denoise_strength)))
        return cv2.fastNlMeansDenoising(
            gray, None, h=h_strength, templateWindowSize=7, searchWindowSize=21
        )

    def adaptive_threshold(self, gray: np.ndarray) -> np.ndarray:
        """
        Soft Sauvola/Gaussian adaptive thresholding scaled to the configured DPI.
        """
        block_size = max(31, int((self.config.dpi / 300.0) * 35))
        if block_size % 2 == 0:
            block_size += 1
        return cv2.adaptiveThreshold(
            gray,
            255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY,
            block_size,
            12,
        )

    def extract_illustrations(
        self, bgr: np.ndarray, gray: np.ndarray, page_number: int
    ) -> List[Dict[str, Any]]:
        """
        Detect non-text photographic or diagram regions on the page, crop them to disk,
        and return their coordinates so the layout analyzer and builder can embed them.
        Excludes full-page borders, decorative frames, and hollow text boxes.
        """
        h, w = gray.shape
        page_area = float(h * w)
        min_area = page_area * self.config.min_illustration_area_ratio
        max_area = page_area * 0.85

        inv = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1]
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (15, 15))
        closed = cv2.morphologyEx(inv, cv2.MORPH_CLOSE, kernel, iterations=2)

        contours, _ = cv2.findContours(
            closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
        )

        illustrations: List[Dict[str, Any]] = []
        img_idx = 0

        for cnt in contours:
            x, y, cw, ch = cv2.boundingRect(cnt)
            area = float(cw * ch)
            if area < min_area or area > max_area:
                continue

            aspect = cw / float(max(1, ch))
            if aspect > 8.0 or aspect < 0.12:
                continue

            # Check if this is a page border frame (spans almost entire page perimeter)
            is_outer_frame = (
                cw > w * 0.85
                and ch > h * 0.85
                and (x < w * 0.1)
                and (y < h * 0.1)
            )
            if is_outer_frame:
                continue

            roi_inv = inv[y : y + ch, x : x + cw]
            ink_density = float(np.count_nonzero(roi_inv)) / area

            # Hollow text box / frame check: border is inked but interior is mostly white
            if cw > 40 and ch > 40:
                inner_y0 = int(ch * 0.15)
                inner_y1 = int(ch * 0.85)
                inner_x0 = int(cw * 0.15)
                inner_x1 = int(cw * 0.85)
                interior_roi = roi_inv[inner_y0:inner_y1, inner_x0:inner_x1]
                interior_density = float(np.count_nonzero(interior_roi)) / max(1, interior_roi.size)
                # If outer box is inked but interior is hollow or typical text density, skip it
                if ink_density < 0.12 and interior_density < 0.08:
                    continue

            # Check if region has high dense ink or continuous tone variance typical of figures/photos
            roi_gray = gray[y : y + ch, x : x + cw]
            std_dev = float(np.std(roi_gray))

            horiz_proj = np.mean(roi_inv > 0, axis=1)
            zero_valleys = int(np.sum(horiz_proj < 0.02))
            valley_ratio = zero_valleys / float(max(1, ch))

            is_graphic = (ink_density > 0.36 and valley_ratio < 0.15) or (
                ink_density > 0.25 and std_dev > 45.0 and valley_ratio < 0.08
            )

            if is_graphic:
                img_idx += 1
                filename = f"page_{page_number:05d}_fig_{img_idx:02d}.png"
                out_path = os.path.join(self.config.extracted_images_dir, filename)
                crop_rgb = cv2.cvtColor(bgr[y : y + ch, x : x + cw], cv2.COLOR_BGR2RGB)
                Image.fromarray(crop_rgb).save(out_path, format="PNG")

                illustrations.append(
                    {
                        "id": f"p{page_number}_fig{img_idx}",
                        "bbox": [int(x), int(y), int(cw), int(ch)],
                        "image_path": out_path,
                        "width_px": int(cw),
                        "height_px": int(ch),
                    }
                )

        illustrations.sort(key=lambda item: item["bbox"][1])
        return illustrations
