# The browser tests: Playwright's own image (Chromium and its system
# libraries) plus the matching Python package and pytest.
FROM mcr.microsoft.com/playwright/python:v1.49.1-noble
RUN pip install --no-cache-dir --break-system-packages playwright==1.49.1 pytest==8.3.4
