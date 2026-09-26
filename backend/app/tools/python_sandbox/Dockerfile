# Sandbox image for the python_analysis tool. It runs ONLY the worker above,
# with `--network none` and resource caps enforced by the docker run flags in
# app/tools/python_analysis.py. No app code is ever reachable from this image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/mplcache \
    PIP_NO_CACHE_DIR=1

RUN pip install --no-cache-dir \
        "RestrictedPython>=8" \
        "numpy>=1.26" \
        "pandas>=2" \
        "scipy>=1.12" \
        "matplotlib>=3.9"

WORKDIR /sandbox
COPY worker.py /sandbox/__worker__.py

# non-root, no home, nothing else writable
USER 65534:65534
CMD ["python", "/sandbox/__worker__.py"]