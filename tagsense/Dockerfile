# Debian (glibc) base: there is no OpenCV wheel for HA's Alpine/musl images.
FROM python:3.12-slim

ARG BUILD_VERSION=dev
ENV TAGSENSE_VERSION=${BUILD_VERSION} \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /opt/tagsense
COPY requirements.txt .
RUN pip install -r requirements.txt \
 && python -c "import cv2, cv2.aruco as a; a.ArucoDetector; a.DICT_APRILTAG_16h5; a.CORNER_REFINE_APRILTAG; print('OpenCV', cv2.__version__)"

COPY app ./app

CMD ["python", "-m", "app.main"]
