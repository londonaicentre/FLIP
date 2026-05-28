# flip-utils

The pip-installable Python library for the FLIP (Federated Learning and Interoperability Platform) ecosystem.
Provides platform logic, NVIDIA FLARE components, Flower helpers, and utility functions consumed by
FL client and server Docker images in federated learning networks.

Part of the [FLIP monorepo](https://github.com/londonaicentre/FLIP). Published on PyPI as `flip-utils`.

## Installation

```bash
pip install flip-utils
```

For full dependencies (ML frameworks, medical imaging):

```bash
pip install flip-utils[full]
```

## Development

```bash
cd flip-utils
uv sync
make unit-test
```
