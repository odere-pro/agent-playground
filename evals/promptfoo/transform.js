// The chassis's response envelope to promptfoo's provider response: the answer text is the
// output; the tool calls, trace id, and status ride along as metadata for the assertions.
module.exports = (json) => ({
  output: (json.output && json.output.text) || '',
  metadata: {
    tool_calls: (json.output && json.output.tool_calls) || [],
    trace_id: json.trace_id,
    status: json.status,
  },
});
