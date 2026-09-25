#!/usr/bin/env bash
set -euo pipefail

# Run on the newly provisioned Ubuntu GPU VM as ubuntu, not on the workstation.
# No credentials are required for the public model checkpoint.
nvidia-smi
sudo apt-get update
sudo apt-get install -y python3-pip python3-venv
sudo install -d -o ubuntu -g ubuntu /opt/kingpro /opt/kingpro/cache
python3 -m venv /opt/kingpro/bootstrap
/opt/kingpro/bootstrap/bin/pip install uv
/opt/kingpro/bootstrap/bin/uv venv --python 3.12 /opt/kingpro/venv
/opt/kingpro/bootstrap/bin/uv pip install \
  --python /opt/kingpro/venv/bin/python 'vllm==0.30.0' --torch-backend=auto
/opt/kingpro/venv/bin/python -c \
  'import torch,vllm; print("vllm",vllm.__version__,"cuda",torch.version.cuda,"gpu",torch.cuda.get_device_name(0))'
sudo install -m 644 /opt/kingpro/kingpro-model.service /etc/systemd/system/kingpro-model.service
sudo systemctl daemon-reload
sudo systemctl enable --now kingpro-model.service
sudo systemctl --no-pager status kingpro-model.service
