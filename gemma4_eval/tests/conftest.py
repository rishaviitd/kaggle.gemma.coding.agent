import copy
import json
from pathlib import Path
import pytest

@pytest.fixture
def trace():
    return {'schema_version':'vllm-model-trace-v1','run':{'task_id':'demo_1'},'turns':[],'final':{'result':{'task_id':'demo_1','resolved':False,'status':'SUCCESS','tool_calls':0,'total_llm_calls':0,'duration_seconds':1,'agent_patch':'','test_exit_code':1}}}

@pytest.fixture
def make_trace(tmp_path,trace):
    def make(data=None,name='model_trace.json',directory=None):
        p=(directory or tmp_path)/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(data if data is not None else trace));return p
    return make

def turn(number=1, calls=None, results=None, messages=None):
    return {'turn':number,'input':{'vllm_request':{'messages':messages or [],'tools':[{'type':'function','function':{'name':'edit_file','parameters':{'type':'object','properties':{'filepath':{'type':'string'},'old_string':{'type':'string'},'new_string':{'type':'string'}},'required':['filepath','old_string','new_string']}}},{'type':'function','function':{'name':'run_command','parameters':{'type':'object','properties':{'command':{'type':'string'}},'required':['command']}}}]}},'output':{'tool_calls':calls or [],'vllm_response_raw':{'created':100+number,'usage':{'prompt_tokens':10,'completion_tokens':5},'choices':[{'finish_reason':'tool_calls','message':{'content':None}}]}},'tool_results':results or []}

def call(cid,name,args,raw=None):
    return {'id':cid,'name':name,'arguments':args,'arguments_raw_json':json.dumps(args) if raw is None else raw}

def result(cid,status='ok',**kwargs):
    return {'tool_call_id':cid,'output':{'status':status,**kwargs}}
