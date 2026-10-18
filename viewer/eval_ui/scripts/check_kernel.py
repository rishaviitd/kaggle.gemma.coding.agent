"""Bounded smoke check for the project Jupyter kernel using local IPC."""
import os
from pathlib import Path
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager
root=Path(__file__).resolve().parents[1]
os.environ['JUPYTER_PATH']=str(root/'.jupyter/share/jupyter')
os.environ['IPYTHONDIR']=str(root/'.jupyter/ipython')
nb=nbformat.v4.new_notebook(cells=[nbformat.v4.new_code_cell("print('kernel verified')")])
km=KernelManager(kernel_name='gemma4-eval',transport='ipc',ip=str(root/'.jupyter/kernel-ipc'))
client=NotebookClient(nb,km=km,timeout=30,startup_timeout=30)
print('Starting IPC kernel',flush=True)
try:
    client.execute()
    print(nb.cells[0].outputs,flush=True)
finally:
    if km.has_kernel:km.shutdown_kernel(now=True)
