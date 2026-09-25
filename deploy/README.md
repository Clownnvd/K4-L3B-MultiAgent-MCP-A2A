# Single-GPU model service

Model: Qwen/Qwen3.5-9B, revision
`c202236235762e1c871ad0ccb60c8ee5ba337b9a`, full tensor count 9,653,104,368.
The service exposes only loopback on the VM. Use an SSH tunnel from the local
workstation (local port 18000 to remote 127.0.0.1:8000). Do not open port 8000
to the public internet. SSH keys and connection details remain in `.local/`.

Copy `kingpro-model.service` and `install_model.sh` to `/opt/kingpro/` on the
new VM, owned by ubuntu, then run the install script. It installs vLLM 0.17.1
with CUDA 12.8 wheels and NVIDIA's CUDA 12.8 forward-compatibility libraries for
the H200's older driver. vLLM 0.30.0 failed to import on the initial CUDA 12.6
environment because its binary required libcudart.so.13. Verify
`/health`, `/v1/models` and a JSON-only inference before running the competition.
Installation scripts are not evidence that deployment succeeded.

Local model configuration (no model credential needed over this private tunnel):

```dotenv
MODEL_CHECKPOINT=Qwen/Qwen3.5-9B
MODEL_SERVED_NAME=Qwen/Qwen3.5-9B
MODEL_BASE_URL=http://127.0.0.1:18000/v1
```

The owner explicitly requested keeping the GPU/model running between tests.
No idle shutdown is configured. The VM continues to incur charges until stopped
through FPT. Record resource and billing details outside public source.
