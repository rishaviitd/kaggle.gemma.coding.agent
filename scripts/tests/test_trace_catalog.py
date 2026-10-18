import importlib.util,json,tempfile,unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('catalog',Path(__file__).parents[1]/'traces'/'build_trace_index.py');catalog=importlib.util.module_from_spec(spec);spec.loader.exec_module(catalog)
class CatalogTests(unittest.TestCase):
 def test_native_only_interrupted_and_completed_are_discoverable(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);remote=root/'logs/remote'
   task=remote/'rishav/run/val/task1';task.mkdir(parents=True)
   (task/'model_trace.json').write_text(json.dumps({'turns':[],'final':{'result':{'resolved':False}}}))
   (task/'gemini_exchanges.json').write_text('[]')
   interrupted=remote/'rishav/run/interrupted/task2';interrupted.mkdir(parents=True)
   (interrupted/'azure_exchanges.json').write_text('[]')
   index=catalog.build_index(root,remote)['iterations']
   self.assertIn('rishav/run/interrupted',index)
   self.assertEqual(index['rishav/run']['val'][0]['native_path'],'logs/remote/rishav/run/val/task1/gemini_exchanges.json')
   self.assertTrue(index['rishav/run/interrupted']['val'][0]['interrupted'])
if __name__=='__main__':unittest.main()
