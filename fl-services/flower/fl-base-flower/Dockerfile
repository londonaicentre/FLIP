FROM flwr/base:${FLWR_VERSION:-1.26.1-py3.12-ubuntu24.04}

WORKDIR /app

# Copy only what we need for dependency install (keeps build cache efficient)
COPY --chown=app:app pyproject.toml .

# Only the non-Flower dependencies are needed.
# Remove the flwr[simulation] dependency from pyproject.toml before installing, since it's only needed for the 
# simulation service, and would pull in extra dependencies that aren't needed for the API service.
RUN sed -i 's/.*flwr\[simulation\].*//' pyproject.toml \
  && python -m pip install -U --no-cache-dir .
