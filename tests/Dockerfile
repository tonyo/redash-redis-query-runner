# Stock Redash plus pytest, so tests import the real redash.query_runner.
FROM redash/redash:26.9.0
USER root
RUN pip install --no-cache-dir pytest==7.4.0
USER redash
