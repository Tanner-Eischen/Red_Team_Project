FROM python:3.12.12-slim-bookworm@sha256:593bd06efe90efa80dc4eee3948be7c0fde4134606dd40d8dd8dbcade98e669c
RUN apt-get update && apt-get install -y --no-install-recommends bash tmux && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 agent
WORKDIR /app
COPY public/ /app/
RUN chown -R agent:agent /app
USER agent
CMD ["/bin/bash"]
