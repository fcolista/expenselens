FROM alpine:3.22

# postgresql16-client must match the server major version (postgres:16-alpine)
RUN apk add --no-cache postgresql16-client age rclone tar gzip python3 tzdata ca-certificates \
    && ln -sf python3 /usr/bin/python

COPY agent.py /app/agent.py

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

ENTRYPOINT ["python", "/app/agent.py"]
CMD ["run"]
