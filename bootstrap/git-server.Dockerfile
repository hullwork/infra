FROM alpine:3.24.1@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b

USER root
RUN apk add --no-cache \
    git-daemon=2.54.0-r0 \
    libcrypto3=3.5.7-r0 \
    libssl3=3.5.7-r0

USER 1000:1000
ENTRYPOINT ["git"]
