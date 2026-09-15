#!/usr/bin/env python3
"""Prototype: convert Claude Code session transcripts (~/.claude/projects/*/*.jsonl) into normalized
'cassettes' (ordered turns: user prompt / assistant text / tool_use / tool_result) and report replayability stats.

Replayability classes (heuristic, per tool call):
  IDEMPOTENT_READ  : Read, Glob, Grep, LS, ToolSearch, ListAgents, and Bash commands that only read (cat/ls/grep/find/git status|log|diff/head/tail/wc/which/echo/python -c 'print'...)
  WORKSPACE_WRITE  : Edit, Write, NotebookEdit, and Bash commands that mutate the workspace (mkdir/cp/mv/sed -i/git add|commit/pip install/npm install/... ) -> replayable in a git-snapshot sandbox
  EXTERNAL         : WebSearch, WebFetch, network Bash (curl/wget/gh api/gh search/pip download), Artifact, Agent/Workflow spawns -> must be served from the recording
  NONDETERMINISTIC : date/time/random/uptime/ps/top, sleep-based polling -> recording is the only sane source
"""
import json, glob, os, re, sys, collections

READ_CMDS = r'^(cat|ls|grep|rg|find|head|tail|wc|which|echo|pwd|stat|file|du|df|tree|jq|sort|uniq|cut|awk|sed -n|git (status|log|diff|show|branch|rev-parse|remote|ls-files)|python3? -c|python3? - ?<<|type|env|printenv|test|\[)'
WRITE_CMDS = r'(mkdir|cp |mv |rm |touch|sed -i|tee |>>?\s*[^&|]|git (add|commit|checkout|init|clone|push|pull|merge|rebase|stash|reset)|pip install|npm (install|i |ci)|uv |brew install|chmod|ln -s|python3? [^-]|node |make |cargo |go )'
NET_CMDS = r'(curl|wget|gh (api|search|repo|pr|issue|auth)|pip download|npm view|ssh |scp |rsync|ping|nslookup|dig )'
NONDET_CMDS = r'(\bdate\b|uptime|ps |top |random|uuidgen|sleep|\$RANDOM|openssl rand|head -c .*urandom)'

def classify(name, inp):
    if name in ('Read','Glob','Grep','LS','ToolSearch','ListAgents','TaskOutput','Monitor'): return 'IDEMPOTENT_READ'
    if name in ('Edit','Write','NotebookEdit','MultiEdit'): return 'WORKSPACE_WRITE'
    if name in ('WebSearch','WebFetch','Artifact','Agent','Workflow','SendMessage','SendUserFile','AskUserQuestion','Skill','TaskStop','CronCreate'): return 'EXTERNAL'
    if name == 'Bash':
        cmd = (inp or {}).get('command','') or ''
        first = cmd.strip().split('\n')[0]
        if re.search(NONDET_CMDS, cmd): return 'NONDETERMINISTIC'
        if re.search(NET_CMDS, cmd): return 'EXTERNAL'
        if re.search(WRITE_CMDS, cmd): return 'WORKSPACE_WRITE'
        if re.match(READ_CMDS, first.strip()): return 'IDEMPOTENT_READ'
        return 'WORKSPACE_WRITE'  # conservative default
    return 'EXTERNAL'

def extract(path):
    turns=[]; pending={}  # tool_use_id -> index in turns
    meta={'path':path,'session_id':None,'cwd':None,'git_branch':None,'model':None,'version':None}
    for line in open(path, encoding='utf-8', errors='ignore'):
        try: r=json.loads(line)
        except: continue
        t=r.get('type')
        if t not in ('user','assistant'): continue
        if r.get('isSidechain'): continue  # subagent transcripts are separate cassettes
        meta['session_id']=meta['session_id'] or r.get('sessionId'); meta['cwd']=meta['cwd'] or r.get('cwd'); meta['git_branch']=meta['git_branch'] or r.get('gitBranch'); meta['version']=meta['version'] or r.get('version')
        m=r.get('message') or {}; c=m.get('content')
        if t=='assistant': meta['model']=meta['model'] or m.get('model')
        if isinstance(c,str): c=[{'type':'text','text':c}]
        if not isinstance(c,list): continue
        for b in c:
            bt=b.get('type')
            if t=='user' and bt=='text' and r.get('origin',{}).get('kind')=='human' or (t=='user' and bt=='text' and not r.get('sourceToolAssistantUUID')):
                turns.append({'kind':'user','text':b.get('text',''),'ts':r.get('timestamp')})
            elif t=='assistant' and bt=='text' and b.get('text','').strip():
                turns.append({'kind':'assistant','text':b['text'],'ts':r.get('timestamp')})
            elif t=='assistant' and bt=='tool_use':
                turns.append({'kind':'tool_use','id':b['id'],'name':b['name'],'input':b.get('input'),'class':classify(b['name'],b.get('input')),'ts':r.get('timestamp')})
                pending[b['id']]=len(turns)-1
            elif t=='user' and bt=='tool_result':
                idx=pending.get(b.get('tool_use_id'))
                content=b.get('content')
                if isinstance(content,list): content=''.join(x.get('text','') if isinstance(x,dict) else str(x) for x in content)
                turns.append({'kind':'tool_result','tool_use_id':b.get('tool_use_id'),'name':turns[idx]['name'] if idx is not None else None,'content':content,'structured':r.get('toolUseResult'),'is_error':bool(b.get('is_error')),'ts':r.get('timestamp')})
    return {'meta':meta,'turns':turns}

if __name__=='__main__':
    root=os.path.expanduser('~/.claude/projects')
    files=sorted(glob.glob(f'{root}/*/*.jsonl'))
    out_dir=sys.argv[1] if len(sys.argv)>1 else None
    if out_dir: os.makedirs(out_dir, exist_ok=True)
    agg=collections.Counter(); per_tool=collections.Counter(); sessions=[]
    for f in files:
        cas=extract(f)
        n_calls=sum(1 for t in cas['turns'] if t['kind']=='tool_use')
        n_user=sum(1 for t in cas['turns'] if t['kind']=='user')
        if n_calls==0: continue
        cls=collections.Counter(t['class'] for t in cas['turns'] if t['kind']=='tool_use')
        agg.update(cls)
        for t in cas['turns']:
            if t['kind']=='tool_use': per_tool[(t['name'],t['class'])]+=1
        # prefix of idempotent-only calls before first non-idempotent call
        prefix=0
        for t in cas['turns']:
            if t['kind']!='tool_use': continue
            if t['class']=='IDEMPOTENT_READ': prefix+=1
            else: break
        sessions.append((os.path.basename(f)[:8], n_user, n_calls, dict(cls), prefix, cas['meta']['model']))
        if out_dir:
            json.dump(cas, open(os.path.join(out_dir, os.path.basename(f).replace('.jsonl','.cassette.json')),'w'), ensure_ascii=False)
    print(f"sessions with tool calls: {len(sessions)}")
    print(f"{'session':10}{'prompts':>8}{'calls':>7}{'idemp-prefix':>14}  classes / model")
    for s in sessions: print(f"{s[0]:10}{s[1]:>8}{s[2]:>7}{s[4]:>14}  {s[3]} {s[5]}")
    tot=sum(agg.values())
    print("\nreplayability class share over", tot, "tool calls:")
    for k,v in agg.most_common(): print(f"  {k:18}{v:6}  {100*v/tot:5.1f}%")
    print("\nper (tool,class):")
    for (n,c),v in per_tool.most_common(20): print(f"  {n:14}{c:18}{v:5}")
