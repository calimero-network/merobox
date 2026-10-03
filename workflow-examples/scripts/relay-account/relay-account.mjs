// The nodeless-account half of `relay-founded-namespace-ha.yml`.
//
// merobox drives nodes; an account with NO node is a key held in a client, so
// its half of the scenario runs here, through mero-js — the client the relay's
// founding (`governance-intents`) and admission (`namespaces/:id/admit`)
// endpoints are built for. Nothing is re-encoded by hand: the warrant, the
// genesis, the invitation and the join op are all mero-js's own signers, so a
// pass here means core accepted what real clients send.
//
// Every subcommand reads and writes one JSON state file (the account keys, the
// namespace, the invitations), so the workflow's steps share it in order. The
// LAST stdout line of a subcommand is a JSON object; the `script` step parses it
// (`json_output: true`) and its `outputs:` export fields to the workflow.
//
//   node relay-account.mjs found  <relay-url> <state>
//   node relay-account.mjs invite <relay-url> <state> <label>
//   node relay-account.mjs claim  <admitter-url> <state> <invitation> <joiner> <nonce> [--refused <text>] [--seen-on <url>]...
//
// Any failed expectation exits non-zero with the reason, which fails the step.

import { readFileSync, writeFileSync, existsSync } from 'node:fs';

import {
  MeroJs,
  RelayClient,
  createMemoryNonceSource,
  generateAccountRoot,
  mintDeviceId,
  signDeviceCert,
  signDeviceScope,
  signerFromSecret,
  signGroupInvitation,
  signMemberJoinOp,
} from '@calimero-network/mero-js';

const TEE_ROLES = new Set(['RelayTee', 'ReadOnlyTee']);
const POLL_TRIES = 60;
const POLL_MS = 500;

const hex = (bytes) => Buffer.from(bytes).toString('hex');
const random = (n) => hex(crypto.getRandomValues(new Uint8Array(n)));
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

function fail(message) {
  console.error(`FAIL: ${message}`);
  process.exit(1);
}

function describeError(err) {
  const parts = [err?.status, err?.message];
  for (const key of ['body', 'data', 'response']) {
    if (err?.[key] !== undefined) {
      parts.push(typeof err[key] === 'string' ? err[key] : JSON.stringify(err[key]));
    }
  }
  return parts.filter((p) => p !== undefined && p !== '').join(' ');
}

function load(stateFile) {
  return existsSync(stateFile) ? JSON.parse(readFileSync(stateFile, 'utf8')) : {};
}

function save(stateFile, state) {
  writeFileSync(stateFile, JSON.stringify(state, null, 2), { mode: 0o600 });
}

function emit(result) {
  // Last line, one object: what `json_output` parses.
  console.log(JSON.stringify(result));
}

/** An account with no node: a root, one device, and the proofs a relay needs. */
async function keyholder() {
  const root = await generateAccountRoot();
  const deviceSecret = random(32);
  const signer = await signerFromSecret(deviceSecret, 'deviceSecret');
  const device = await mintDeviceId(root.accountId, crypto.getRandomValues(new Uint8Array(16)));
  const credential = await signDeviceCert({
    rootSecret: root.secret,
    device,
    signPublicKey: signer.publicKey,
    kemPublicKey: random(32),
    deviceEpoch: 0,
  });
  const scope = await signDeviceScope({ rootSecret: root.secret, device });
  return { account: root.accountId, deviceSecret, credential, scope };
}

function admin(url) {
  return new MeroJs({ baseUrl: url, timeoutMs: 60_000 }).admin;
}

async function members(url, groupId) {
  return (await admin(url).listGroupMembers(groupId)).members;
}

async function waitForMember(url, groupId, account, label) {
  let last = [];
  for (let i = 0; i < POLL_TRIES; i += 1) {
    last = await members(url, groupId).catch(() => []);
    const row = last.find((m) => m.identity === account);
    if (row) return row;
    await sleep(POLL_MS);
  }
  fail(`${label} (${account}) never appeared in ${groupId} on ${url}: ${JSON.stringify(last)}`);
}

// ---------------------------------------------------------------------------

async function found(relayUrl, stateFile) {
  const relayIdentity = await admin(relayUrl).getNodeIdentity();
  const founder = await keyholder();

  const relay = new RelayClient({
    relayUrl,
    authorAccount: founder.account,
    authorProof: founder.credential,
    deviceSecret: founder.deviceSecret,
    nonces: createMemoryNonceSource(1),
    timeoutMs: 60_000,
  });
  // The namespace does not exist yet, so nothing can be asked about it: the
  // executor is the relay's own account, read off the relay.
  const founded = await relay
    .foundNamespace({ executorAccount: relayIdentity.accountId })
    .catch((err) => fail(`foundNamespace refused: ${describeError(err)}`));
  console.log(`founded ${founded.namespaceId}: ${JSON.stringify(founded)}`);

  if (founded.teeEnabled !== true) {
    fail(`founding did not enable TEE on the relay: ${founded.teeError ?? '(no teeError)'}`);
  }

  const founderRow = await waitForMember(relayUrl, founded.namespaceId, founder.account, 'founder');
  if (founderRow.role !== 'Admin') fail(`founder is ${founderRow.role}, not Admin`);
  const relayRow = await waitForMember(
    relayUrl,
    founded.namespaceId,
    relayIdentity.accountId,
    'founding relay',
  );
  if (relayRow.role !== 'RelayTee') fail(`founding relay is ${relayRow.role}, not RelayTee`);
  console.log(`founder ${founder.account} is Admin; relay ${relayIdentity.accountId} is RelayTee`);

  save(stateFile, {
    namespaceId: founded.namespaceId,
    relayAccount: relayIdentity.accountId,
    founder,
    joiners: {},
    invitations: {},
  });
  emit({
    namespaceId: founded.namespaceId,
    relayAccount: relayIdentity.accountId,
    founderAccount: founder.account,
    teeEnabled: founded.teeEnabled,
  });
}

async function invite(relayUrl, stateFile, label) {
  const state = load(stateFile);
  const { namespaceId, founder } = state;
  if (!namespaceId) fail(`no namespace in ${stateFile}; run 'found' first`);

  // What a node minting with no explicit admitters would name (core's
  // `default_admitters`): the admins, plus every TEE the namespace admitted.
  // Computed from the member list, because the founder has no node to mint on.
  const rows = await members(relayUrl, namespaceId);
  const admitters = [
    ...new Set(rows.filter((m) => m.role === 'Admin' || TEE_ROLES.has(m.role)).map((m) => m.identity)),
  ].sort();

  // The founder's device signs the invitation; peers resolve it to the founder
  // through the namespace's device bindings. Carry the link through the relay —
  // a no-op (`alreadyBound`) if founding already bound it.
  const link = await admin(relayUrl)
    .linkAccountDevice(namespaceId, { credential: founder.credential, scope: founder.scope })
    .catch((err) => fail(`relay would not carry the founder's device link: ${describeError(err)}`));
  console.log(`founder device link: ${JSON.stringify(link)}`);

  const invitation = await signGroupInvitation({
    groupId: namespaceId,
    inviterAccount: founder.account,
    deviceSecret: founder.deviceSecret,
    admitters,
  });
  state.invitations[label] = invitation;
  save(stateFile, state);
  console.log(`invitation '${label}' names admitters ${JSON.stringify(admitters)}`);
  emit({ label, admitters: admitters.join(','), admitterCount: admitters.length });
}

async function claim(admitterUrl, stateFile, label, joinerLabel, nonce, opts) {
  const state = load(stateFile);
  const { namespaceId } = state;
  const invitation = state.invitations?.[label];
  if (!invitation) fail(`no invitation '${label}' in ${stateFile}`);

  state.joiners[joinerLabel] ??= await keyholder();
  const joiner = state.joiners[joinerLabel];
  save(stateFile, state);

  const signedOp = await signMemberJoinOp({
    namespaceId,
    member: joiner.account,
    invitation,
    credential: joiner.credential,
    deviceSecret: joiner.deviceSecret,
    nonce: Number(nonce),
  });

  let outcome;
  try {
    outcome = { ok: true, value: await admin(admitterUrl).admitJoin(namespaceId, { invitation, signedOp }) };
  } catch (err) {
    outcome = { ok: false, error: describeError(err) };
  }

  if (opts.refused !== undefined) {
    if (outcome.ok) {
      fail(`expected ${admitterUrl} to refuse '${label}' with "${opts.refused}", but it admitted: ${JSON.stringify(outcome.value)}`);
    }
    if (!outcome.error.includes(opts.refused)) {
      fail(`refused for the wrong reason: wanted "${opts.refused}", got: ${outcome.error}`);
    }
    console.log(`refused as expected: ${outcome.error}`);
    emit({ label, joiner: joiner.account, refused: true, reason: outcome.error });
    return;
  }

  if (!outcome.ok) fail(`admit of '${label}' by ${admitterUrl} refused: ${outcome.error}`);
  if (outcome.value?.published !== true) fail(`admit did not publish: ${JSON.stringify(outcome.value)}`);

  const row = await waitForMember(admitterUrl, namespaceId, joiner.account, joinerLabel);
  if (row.role !== 'Member') fail(`${joinerLabel} joined as ${row.role}, not Member`);
  for (const url of opts.seenOn) {
    await waitForMember(url, namespaceId, joiner.account, `${joinerLabel} (replicated)`);
    console.log(`${joinerLabel} is visible on ${url}`);
  }
  console.log(`${joinerLabel} ${joiner.account} joined through ${admitterUrl}`);
  emit({ label, joiner: joiner.account, refused: false, role: row.role });
}

// ---------------------------------------------------------------------------

function parseClaimOpts(rest) {
  const opts = { refused: undefined, seenOn: [] };
  for (let i = 0; i < rest.length; i += 1) {
    if (rest[i] === '--refused') opts.refused = rest[++i];
    else if (rest[i] === '--seen-on') opts.seenOn.push(rest[++i]);
    else fail(`unknown option ${rest[i]}`);
  }
  return opts;
}

const [cmd, ...args] = process.argv.slice(2);
try {
  switch (cmd) {
    case 'found':
      await found(args[0], args[1]);
      break;
    case 'invite':
      await invite(args[0], args[1], args[2]);
      break;
    case 'claim':
      await claim(args[0], args[1], args[2], args[3], args[4], parseClaimOpts(args.slice(5)));
      break;
    default:
      fail(`unknown subcommand '${cmd}' (found | invite | claim)`);
  }
} catch (err) {
  fail(`${cmd}: ${describeError(err)}\n${err?.stack ?? ''}`);
}
