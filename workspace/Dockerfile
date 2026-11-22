FROM scratch AS assets
ADD agy.tgz /assets/agy/
ADD codex.tgz /assets/codex/
ADD claude.tgz /assets/claude/

FROM aiwork-devbase:1
COPY --from=assets /assets/agy/antigravity /usr/local/bin/agy
COPY --from=assets /assets/claude/package/claude /usr/local/bin/claude
COPY --from=assets /assets/codex/package/vendor/aarch64-unknown-linux-musl /opt/codex
RUN chmod 755 /usr/local/bin/agy /usr/local/bin/claude /opt/codex/bin/codex && ln -s /opt/codex/bin/codex /usr/local/bin/codex
COPY aiwork-sessions /usr/local/bin/aiwork-sessions
COPY dow-codex dow-mac install-mac-tool /usr/local/bin/
COPY aiwork-gui-pty /usr/local/bin/aiwork-gui-pty
COPY aiwork-grid /usr/local/bin/aiwork-grid
COPY browser-open /usr/local/bin/browser-open
COPY aiwork-native-requests /usr/local/bin/aiwork-native-requests
RUN ln -s browser-open /usr/local/bin/xdg-open && ln -s browser-open /usr/local/bin/www-browser
ENV BROWSER=/usr/local/bin/browser-open
COPY exit-ip /usr/local/bin/exit-ip
ENV DISABLE_AUTOUPDATER=1
ENV http_proxy=http://127.0.0.1:7890 https_proxy=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890 HTTPS_PROXY=http://127.0.0.1:7890 ALL_PROXY=http://127.0.0.1:7890 NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1
ARG DEV_UID=1000
RUN if [ "$(id -u node)" != "$DEV_UID" ]; then usermod -u "$DEV_UID" node; fi
USER node
WORKDIR /work
ENTRYPOINT ["socat","TCP4-LISTEN:7890,bind=127.0.0.1,reuseaddr,fork","UNIX-CONNECT:/link/out.sock"]
