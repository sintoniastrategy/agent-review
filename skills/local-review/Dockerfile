FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git libstdc++6 ripgrep && rm -rf /var/lib/apt/lists/*
COPY scripts /opt/agr/scripts
COPY runtime-release.json /opt/agr/runtime-release.json
RUN chmod -R a+rX /opt/agr/scripts && chmod a+r /opt/agr/runtime-release.json && python3 /opt/agr/scripts/install_claude.py /opt/agr/bin/claude
ENV PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python3", "/opt/agr/scripts/review.py"]
