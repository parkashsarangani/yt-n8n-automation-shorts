#!/usr/bin/env node
// Compatibility entry point. Tests own no private/obsolete migration chain.
const {execFileSync}=require('child_process');
const path=require('path');
const root=path.resolve(__dirname,'..');
execFileSync(process.execPath,['--test','shorts-compose/tests/writer-publication-contract.test.js','shorts-compose/tests/coherence-contracts.test.js'],{cwd:root,stdio:'inherit'});
