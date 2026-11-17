# The server and the data feeder, as on the Pi (Debian Trixie, Python from
# Debian), plus libfaketime for the fake clock. The repo is mounted at /app.
FROM debian:trixie-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 faketime tzdata curl \
 && rm -rf /var/lib/apt/lists/*
