import cv2
import numpy as np
import pytest

from app.detector import Detector
from app.tagprint import tag_png


@pytest.mark.parametrize("tag", [0, 5, 29])
def test_printed_tag_decodes(tag):
    img = cv2.imdecode(np.frombuffer(tag_png(tag, cell_px=30), np.uint8), cv2.IMREAD_COLOR)
    det = Detector(tag).detect(img, (0, 0, 1, 1))
    assert det.found


def test_bad_tag_id():
    with pytest.raises(ValueError):
        tag_png(30)
