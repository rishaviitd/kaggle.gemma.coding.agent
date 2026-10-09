"""Execute only our authored analytics notebook; no trace code is executed."""
import os
from pathlib import Path
import nbformat
from nbclient import NotebookClient
from jupyter_client import KernelManager
root=Path(__file__).resolve().parents[1]
os.environ['JUPYTER_PATH']=str(root/'.jupyter/share/jupyter')
os.environ['IPYTHONDIR']=str(root/'.jupyter/ipython')
os.environ['JUPYTER_RUNTIME_DIR']=str(root/'.jupyter/runtime')
nb=nbformat.read(root/'notebooks/01_agent_trace_report.ipynb',as_version=4)
km=KernelManager(kernel_name='gemma4-eval',transport='ipc',ip=str(root/'.jupyter/report-ipc'))
client=NotebookClient(nb,km=km,timeout=120,startup_timeout=30,resources={'metadata':{'path':str(root/'notebooks')}})
client.on_cell_start=lambda cell,cell_index: print(f'Cell {cell_index+1}/{len(nb.cells)}',flush=True)
try:
    client.execute()
finally:
    if km.has_kernel:km.shutdown_kernel(now=True)
nbformat.write(nb,root/'notebooks/01_agent_trace_report.executed.ipynb')
errors=sum(o.output_type=='error' for c in nb.cells if c.cell_type=='code' for o in c.outputs)
print('Notebook executed:',sum(c.cell_type=='code' for c in nb.cells),'cells; errors:',errors)
