FROM python:3.14.8-slim-bookworm@sha256:a66d3a463d0be3cc537c269b0d3418746dda512417a975559a0eaa8a6406baaf AS sqlite
RUN apt-get update && apt-get install --no-install-recommends -y gcc libc6-dev ca-certificates && rm -rf /var/lib/apt/lists/*
WORKDIR /build
ADD https://www.sqlite.org/2026/sqlite-autoconf-3530200.tar.gz /build/sqlite.tar.gz
RUN echo '588ad51949419a56ebe81fe56193d510c559eb94c9a57748387860b5d3069316  sqlite.tar.gz' | sha256sum -c - \
 && tar -xzf sqlite.tar.gz && mkdir -p /opt/sqlite/lib \
 && gcc -O1 -fPIC -shared -DSQLITE_THREADSAFE=1 -DSQLITE_ENABLE_COLUMN_METADATA=1 -DSQLITE_ENABLE_FTS5=1 -DSQLITE_ENABLE_RTREE=1 \
    -Wl,-soname,libsqlite3.so.0 -o /opt/sqlite/lib/libsqlite3.so.0 sqlite-autoconf-3530200/sqlite3.c -lm -ldl -pthread

FROM python:3.14.8-slim-bookworm@sha256:a66d3a463d0be3cc537c269b0d3418746dda512417a975559a0eaa8a6406baaf AS runtime
ENV LD_LIBRARY_PATH=/opt/sqlite/lib PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY --from=sqlite /opt/sqlite /opt/sqlite
RUN python -c "import sqlite3; assert sqlite3.sqlite_version == '3.53.2', sqlite3.sqlite_version"
WORKDIR /app
COPY requirements/runtime.lock requirements/production-linux-amd64-cp314.lock /app/requirements/
RUN python -m pip install --no-cache-dir --only-binary=:all: --require-hashes -r requirements/production-linux-amd64-cp314.lock
COPY . /app
RUN chmod -R a+rX /app \
 && BOOKLIST_ENV=development BOOKLIST_DATA_DIR=/tmp/static-build python manage.py collectstatic --noinput \
 && groupadd --gid 10001 booklist && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin booklist
USER 10001:10001
CMD ["gunicorn", "--config", "deploy/gunicorn.conf.py", "config.wsgi:application"]

FROM python:3.14.8-slim-bookworm@sha256:a66d3a463d0be3cc537c269b0d3418746dda512417a975559a0eaa8a6406baaf AS verification_tools
RUN apt-get update && apt-get install --no-install-recommends -y nodejs && rm -rf /var/lib/apt/lists/*

FROM verification_tools AS verification
ENV LD_LIBRARY_PATH=/opt/sqlite/lib PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
# Both stages use the same pinned base. Test the runtime's exact Python,
# installed packages, SQLite library and application, with Node added separately.
COPY --from=runtime /usr/local /usr/local
COPY --from=runtime /opt/sqlite /opt/sqlite
COPY --from=runtime /app /app
WORKDIR /app
RUN groupadd --gid 10001 booklist && useradd --uid 10001 --gid 10001 --no-create-home --shell /usr/sbin/nologin booklist
USER 10001:10001
ENV BOOKLIST_ENV=test BOOKLIST_DATA_DIR=/tmp/verification BOOKLIST_LEGACY_TESTS=1
CMD ["python", "manage.py", "test", "booklist.tests", "--noinput"]
