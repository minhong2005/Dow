FROM scratch AS proxy
ADD singbox.tgz /assets/
FROM aiwork-devbase:1
COPY --from=proxy /assets/sing-box-1.14.4-linux-arm64/sing-box /usr/local/bin/sing-box
RUN mkdir /config
COPY start.sh /usr/local/bin/start
COPY guard.py /usr/local/bin/gateway-guard.py
ENTRYPOINT ["/bin/sh","/usr/local/bin/start"]
