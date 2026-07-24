import { parseSSE } from '../../frontend/src/chatApi.js'

const input =
  'data: {"type":"thinking","step":1}\n\n' +
  'data: {"type":"answer","text":"hi"}\n\n' +
  'data: {"type":"part'   // 故意不完整的尾段

const { events, rest } = parseSSE(input)
console.log('events:', events.length)
console.log('types:', events.map((e) => e.type).join(','))
console.log('incomplete tail retained:', rest.length > 0)
