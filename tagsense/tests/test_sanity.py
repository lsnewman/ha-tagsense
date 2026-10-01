import cv2
import numpy as np

from app.sanity import check_frame, check_image

CROP = (0.0, 0.0, 1.0, 1.0)


def jpeg(img):
    return cv2.imencode(".jpg", img)[1].tobytes()


def noise(seed=0, mean=110, sd=30):
    rng = np.random.default_rng(seed)
    return rng.normal(mean, sd, (360, 640, 3)).clip(0, 255).astype(np.uint8)


def test_textured_frame_passes():
    c = check_frame(jpeg(noise()), CROP)
    assert c.ok and c.reason == "ok" and c.resolution == "640x360"


def test_vertical_smear_fails():
    row = noise()[:1]
    smear = np.repeat(row, 360, axis=0)
    c = check_image(smear, CROP)
    assert not c.ok and c.reason == "smeared" and c.ratio < 0.1


def test_dark_low_texture_frame_passes():
    dark = cv2.GaussianBlur(noise(sd=30), (9, 9), 3)
    dark = (dark.astype(np.float32) * 0.08).astype(np.uint8)   # mean ~9
    c = check_image(dark, CROP)
    assert c.ok, (c.v, c.h, c.ratio)


def test_dark_smear_still_fails():
    row = (noise()[:1].astype(np.float32) * 0.1).astype(np.uint8)
    c = check_image(np.repeat(row, 360, axis=0), CROP)
    assert not c.ok and c.reason == "smeared"


def test_uniform_frame_is_flat():
    c = check_image(np.zeros((360, 640, 3), np.uint8), CROP)
    assert not c.ok and c.reason == "flat"


def test_garbage_bytes_fail():
    assert check_frame(b"not a jpeg", CROP).reason == "decode_failed"
    assert check_frame(b"", CROP).reason == "decode_failed"
