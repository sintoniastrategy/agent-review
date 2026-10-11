FROM python:3.12-slim-bookworm
ARG REVIEW_AGENT=claude
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl git libstdc++6 ripgrep && rm -rf /var/lib/apt/lists/*
COPY scripts /opt/agr/scripts
COPY runtime-release.json /opt/agr/
RUN chmod -R a+rX /opt/agr/scripts && chmod a+r /opt/agr/runtime-release.json && if [ "$REVIEW_AGENT" = codex ]; then python3 /opt/agr/scripts/install_codex.py /opt/agr/codex; else python3 /opt/agr/scripts/install_claude.py /opt/agr/bin/claude; fi
ENV PYTHONUTF8=1 PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["python3", "/opt/agr/scripts/review.py"]
