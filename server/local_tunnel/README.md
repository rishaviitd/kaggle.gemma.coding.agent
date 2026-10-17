Run `./start.sh` or `./start.sh --local` for laptop-only access. It starts vLLM
on `10.55.1.6`, forwards it to `127.0.0.1:18000` over SSH, and exposes the
logged API at `http://127.0.0.1:18001/v1`.

Run `./start.sh --ngrok` when another device needs access. This starts the same
local setup and adds the ngrok public URL.

Requests to `/v1/chat/completions` need `Authorization: Bearer <VLLM_API_KEY>`.
The key is in `.env`; do not share the ngrok authtoken.
The proxy records the caller IP in the laptop's `requests.log`, then removes
caller IP forwarding headers before sending HTTP requests through SSH. The SSH
connection itself still exposes this laptop's IP to the H100 machine.

macOS runs SSH and the selected tunnel components as background services. `start.sh` copies their
runtime files into your user Library and the SSH key into `~/.ssh` with
restricted permissions.

Run `./stop.sh` to stop all laptop tunnel services. The H100 server continues
running. The public URL works only in ngrok mode while this laptop is on.
