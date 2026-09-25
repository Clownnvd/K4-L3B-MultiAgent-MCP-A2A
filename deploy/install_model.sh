#!/usr/bin/env bash
set -euo pipefail

# Run on the newly provisioned Ubuntu GPU VM as ubuntu, not on the workstation.
# No credentials are required for the public model checkpoint.
nvidia-smi
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv cuda-compat-12-8 ninja-build build-essential
sudo install -d -o ubuntu -g ubuntu /opt/kingpro /opt/kingpro/cache
if [ ! -x /opt/kingpro/bootstrap/bin/python ]; then
  python3 -m venv /opt/kingpro/bootstrap
fi
/opt/kingpro/bootstrap/bin/pip install uv
if [ ! -x /opt/kingpro/venv/bin/python ]; then
  /opt/kingpro/bootstrap/bin/uv venv --python 3.12 /opt/kingpro/venv
fi
/opt/kingpro/bootstrap/bin/uv pip install \
  --python /opt/kingpro/venv/bin/python 'vllm==0.17.1' --torch-backend=cu128
export LD_LIBRARY_PATH=/usr/local/cuda-12.8/compat
/opt/kingpro/venv/bin/python -c \
  'import torch,vllm; print("vllm",vllm.__version__,"cuda",torch.version.cuda,"gpu",torch.cuda.get_device_name(0))'
sudo install -m 644 /opt/kingpro/kingpro-model.service /etc/systemd/system/kingpro-model.service
sudo systemctl daemon-reload
sudo systemctl enable --now kingpro-model.service
sudo systemctl --no-pager status kingpro-model.service
