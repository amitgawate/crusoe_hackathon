"""Exercise the browser's pure diff/replay code without a browser or live APIs."""
import re
import subprocess
from pathlib import Path


def js_check(assertions, controller=False):
    html = Path('static/improvement.html').read_text()
    match = re.search(r'<script id="demo-helpers">(.*?)</script>', html, re.S)
    assert match, 'UI helper script is missing'
    if controller:
        main = html[html.index('async function poll()'):html.index("$('start').onclick")]
    else:
        main = ''
    source = "const assert = require('node:assert/strict');\n" + match[1] + '\n' + main + '\n' + assertions
    result = subprocess.run(['node'], input=source, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_prompt_diff_preserves_exact_text_and_marks_additions_and_deletions():
    js_check('''
const before = 'Use evidence.\\nSay false when missing.';
const after = 'Use evidence.\\nSay unverifiable when missing.';
const d = diffWords(before, after);
assert.equal(d.filter(x=>x.type!=='added').map(x=>x.text).join(''),before);
assert.equal(d.filter(x=>x.type!=='removed').map(x=>x.text).join(''),after);
assert.equal(d.filter(x=>x.type==='added').map(x=>x.text).join(''),'unverifiable');
assert.equal(d.filter(x=>x.type==='removed').map(x=>x.text).join(''),'false');
assert.ok(diffWords('<script> x', '<script> x').every(x=>x.type==='same'));
assert.equal(diffWords('', 'hello')[0].type,'added');
assert.equal(diffWords('hello', '')[0].type,'removed');
''')


def test_replay_reveals_saved_results_in_order_without_mutating_source():
    js_check('''
const rows = [{id:'a',correct:true},{id:'b',correct:false,error:'timeout'}];
const scored = {rows,correct:1,completed:2,total:2,accuracy:50,errors:1};
const saved = {status:'complete',stage:'candidate_test',candidate_prompt:'NEW',diagnosis:'LESSON',
 outcome:'FINAL',calls:63,tokens:999,events:[],results:{baseline_dev:scored,candidate_dev:scored,baseline_test:scored,candidate_test:scored}};
const original=JSON.stringify(saved), frames=replayFrames(saved);
assert.ok(frames.length>8);
assert.deepEqual(frames[0].results,{});
assert.equal(frames[0].candidate_prompt,null);
assert.equal(frames[0].outcome,null);
const partial=frames.find(f=>f.results.baseline_dev?.completed===1);
assert.equal(partial.results.baseline_dev.accuracy,100);
assert.equal(partial.results.baseline_dev.total,2);
assert.equal(partial.results.baseline_test,undefined);
assert.equal(partial.candidate_prompt,null);
const revised=frames.find(f=>f.candidate_prompt==='NEW');
assert.equal(revised.results.baseline_dev.completed,2);
assert.equal(revised.results.baseline_test,undefined);
assert.deepEqual(frames.at(-1),saved);
frames[1].results.injected=true;
assert.equal(JSON.stringify(saved),original);
assert.deepEqual(replayFrames({...saved,status:'running'}),[]);
''')


def test_no_candidate_replay_never_invents_a_prompt_or_scores():
    js_check('''
const scored={rows:[{correct:true}],correct:1,completed:1,total:1,accuracy:100,errors:0};
const saved={status:'complete',candidate_prompt:null,diagnosis:'No failures',events:[],results:{baseline_dev:scored,baseline_test:scored}};
const frames=replayFrames(saved);
assert.ok(frames.every(f=>!f.candidate_prompt&&!f.results.candidate_dev&&!f.results.candidate_test));
assert.deepEqual(frames.at(-1),saved);
''')



def test_controller_recovers_controls_and_preserves_replay_during_polling():
    js_check("""
const elements=new Map();
const $=id=>{if(!elements.has(id))elements.set(id,{textContent:'',disabled:false});return elements.get(id)};
let latest={status:'complete'},state=latest,busy=false,playback=null;
function render(){
  $('start').disabled=busy||state.status==='running';
  $('replay').disabled=busy||latest.status!=='complete';
}
function setTimeout(){};
(async()=>{
  let fails=true;
  globalThis.fetch=async()=>{if(fails)throw Error('offline');return {ok:true,json:async()=>({status:'complete'})}};
  await poll();assert.equal($('start').disabled,true);
  fails=false;await poll();assert.equal($('connection').textContent,'● Connected');assert.equal($('start').disabled,false);
  globalThis.fetch=async()=>({ok:false,json:async()=>({detail:'Rejected'})});
  await action('start');assert.equal(busy,false);assert.equal($('replay').disabled,false);
  assert.equal($('notice').textContent,'Rejected');
  playback={};const replayState={status:'running'};state=replayState;
  globalThis.fetch=async()=>({ok:true,json:async()=>({status:'complete',id:'newer'})});
  await poll();assert.equal(state,replayState);assert.equal(latest.id,'newer');
})().catch(e=>{console.error(e);process.exitCode=1});
""", controller=True)
