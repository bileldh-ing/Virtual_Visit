# Third-party notices

RoomScape AI is licensed under Apache-2.0. It uses the following third-party software and data.

## Reconstruction
| Component | Licence | Use |
|---|---|---|
| [COLMAP](https://colmap.github.io/) | BSD-3-Clause | Structure-from-motion (installed as a system package in the worker image; not vendored) |
| [OpenCV](https://opencv.org/) (`opencv-python-headless`) | Apache-2.0 | Capture-quality checks (blur, ORB overlap) |
| [NumPy](https://numpy.org/) | BSD-3-Clause | Geometry and point processing |
| [Pillow](https://python-pillow.org/) | MIT-CMU (HPND) | Image decoding, derivatives, preview rendering |

## Sample data
| Dataset | Licence | Use |
|---|---|---|
| [Eyeful Tower](https://github.com/facebookresearch/EyefulTower), scene `apartment` © Meta Platforms, Inc. and affiliates | MIT | Test fixture and demo project (downloaded on demand, not committed) |

## API / worker
FastAPI (MIT), Starlette (BSD-3), Uvicorn (BSD-3), SQLAlchemy (MIT), Alembic (MIT), psycopg (LGPL-3.0, dynamically linked),
Pydantic (MIT), argon2-cffi (MIT), RQ (BSD-2), redis-py (MIT), boto3 (Apache-2.0), httpx (BSD-3), python-multipart (Apache-2.0).

## Web
Next.js (MIT), React (MIT), Three.js (MIT), @react-three/fiber and drei (MIT), framer-motion (MIT), TanStack Query (MIT),
Radix UI (MIT), lucide-react (ISC), Tailwind CSS (MIT), zod (MIT), clsx (MIT).

Full licence texts are distributed with each package (`node_modules/*/LICENSE`, `site-packages/*/LICENSE*`).
