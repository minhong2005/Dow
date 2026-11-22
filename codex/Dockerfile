FROM aiwork-tools:1
USER root
COPY start /usr/local/bin/dow-codex-server
COPY browser-open /usr/local/bin/browser-open
RUN ln -sf /opt/codex/codex-resources/bwrap /usr/local/bin/bwrap && ps -p 1 >/dev/null && \
    ln -sf browser-open /usr/local/bin/xdg-open && ln -sf browser-open /usr/local/bin/www-browser && \
    mkdir -p /home/node/.codex /home/node/.local/state/aiwork /run/dow-codex
ENV BROWSER=/usr/local/bin/browser-open
USER node
WORKDIR /work
ENTRYPOINT ["/usr/local/bin/dow-codex-server"]
