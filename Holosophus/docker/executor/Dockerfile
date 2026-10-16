# Keep the established CUDA/ML stack; add the small document/chemistry toolset.
FROM phoenix120/holosophos_mle@sha256:be537e378315cb8796163ad5aabbc11077003e7afa141c9141f35a42091b4862

USER root
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
       ca-certificates curl jq file zip unzip poppler-utils fontconfig fonts-dejavu-core bsdextrautils \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt check_environment.py /opt/holosophus/executor/
# The base already supplies NumPy and Pillow. Do not upgrade the ML environment.
RUN python -m pip install --no-cache-dir --no-deps --require-hashes \
       -r /opt/holosophus/executor/requirements.txt \
    && python -m pip freeze > /opt/holosophus/executor/python-packages.txt \
    && dpkg-query -W > /opt/holosophus/executor/system-packages.txt \
    && python /opt/holosophus/executor/check_environment.py

# The production adapter sets the host user's UID/GID for every session.
LABEL org.opencontainers.image.title="AI Researcher command executor" \
      org.opencontainers.image.version="2026-10-10"
