// Reads one JSON {"text"} per line on stdin; prints one JSON result per line for each competitor.
import { redactText } from './claude-code-redact/plugins/redact/hooks/scrub'
import { scan } from './sensitive-canary/src/lib/rules.ts'
import * as readline from 'node:readline'

const rl = readline.createInterface({ input: process.stdin })
rl.on('line', (line) => {
  const { text } = JSON.parse(line)
  const off = new Set<string>()
  const ccrSecrets = redactText(text, 'bench', { pii: false, quiet: true, off }).text
  const ccrPii = redactText(text, 'bench', { pii: true, quiet: true, off }).text
  let canary: { blocked: boolean; error?: string }
  try { canary = { blocked: scan(text).length > 0 } } catch (e) { canary = { blocked: true, error: String(e) } }
  process.stdout.write(JSON.stringify({ ccrSecrets, ccrPii, canary }) + '\n')
})
