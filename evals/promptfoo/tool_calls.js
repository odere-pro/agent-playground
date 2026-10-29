// The tools the engine called, in order, with their arguments, against the case's
// `expected_tool_calls` var. The calls come from the response envelope (transform.js).
module.exports = (output, context) => {
  const meta = (context.providerResponse && context.providerResponse.metadata) || {};
  const got = (meta.tool_calls || []).map((c) => ({ name: c.name, arguments: c.arguments }));
  const want = context.vars.expected_tool_calls || [];
  const same = JSON.stringify(got) === JSON.stringify(want);
  return {
    pass: same,
    score: same ? 1 : 0,
    reason: same ? 'tool calls match' : `tool calls ${JSON.stringify(got)}, expected ${JSON.stringify(want)}`,
  };
};
