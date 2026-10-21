FROM rustfs/rustfs:latest

USER 0:0
RUN mkdir -p /data && chown 10001:10001 /data
USER 10001:10001
