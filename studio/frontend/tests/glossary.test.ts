import { describe, expect, it } from 'vitest'
import { PARAM_GLOSSARY, UNKNOWN_HINT } from '../src/glossary'

// Обязательные ключи глоссария параметров (спека дня 11, D7)
const REQUIRED_KEYS = [
  'model',
  'messages',
  'role',
  'content',
  'temperature',
  'top_p',
  'max_tokens',
  'stream',
  'stop',
  'seed',
  'n',
  'frequency_penalty',
  'presence_penalty',
  'logprobs',
  'top_logprobs',
  'tools',
  'tool_choice',
  'response_format',
  'user',
  'service_tier',
  'logit_bias',
  'stream_options',
  'include_usage',
  'prompt_tokens',
  'completion_tokens',
  'total_tokens',
  'reasoning_tokens',
  'prompt_tokens_details',
  'completion_tokens_details',
] as const

describe('PARAM_GLOSSARY', () => {
  it.each(REQUIRED_KEYS)('имеет пояснение для %s (непустая строка)', (key) => {
    const note = PARAM_GLOSSARY[key]
    expect(typeof note).toBe('string')
    expect(note.trim().length).toBeGreaterThan(0)
  })
})

describe('UNKNOWN_HINT', () => {
  it('точная строка для неизвестных параметров', () => {
    expect(UNKNOWN_HINT).toBe('(доп. параметр, без описания)')
  })
})
