import('./frontend/src/chatApi.js').then(({parseSSE})=>{
  const {events,rest}=parseSSE('data: {"type":"thinking","step":1}\n\ndata: {"type":"answer","text":"hi"}\n\ndata: {"type":"part');
  console.log('events',events.length,'types',events.map(e=>e.type).join(','),'rest?',rest.length>0)
})
