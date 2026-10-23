FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY driver_wattcycle/ driver_wattcycle/
COPY manifest.json driver_wattcycle/manifest.json
COPY config.default.ini .

# The git release tag, passed by release.yml; the only version source.
# Unset (a local build) the device reports `dev`.
ARG DEVICE_VERSION
ENV DEVICE_VERSION=${DEVICE_VERSION}

ENTRYPOINT ["python", "-m", "driver_wattcycle.main"]
CMD ["--config", "/app/config.ini"]
