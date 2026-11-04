# The disposable environment that runs AI-generated code.
# It holds no secrets and no network tools beyond what the base image ships.
FROM python:3.11-slim

# --no-cache-dir keeps the image small. Versions are pinned in requirements.txt.
COPY requirements.txt /sandbox/requirements.txt
RUN pip install --no-cache-dir --no-compile -r /sandbox/requirements.txt

COPY runner.py /sandbox/runner.py

# Run as an ordinary user with no home folder and no login shell.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin sandbox
USER 10001:10001

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /tmp
ENTRYPOINT ["python", "/sandbox/runner.py"]
