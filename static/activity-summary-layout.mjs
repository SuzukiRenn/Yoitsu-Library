export const WIDTH=1080, HEIGHT=1600;
const LEFT=76, RIGHT=1004, BOTTOM=1504;
export const FONT='system-ui, "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif';

export function wrap(text,width,measure) {
  const output=[];
  for (const paragraph of String(text ?? '').replace(/\r\n?/g,'\n').split('\n')) {
    const letters=typeof Intl.Segmenter==='function' ? Array.from(new Intl.Segmenter('zh',{granularity:'grapheme'}).segment(paragraph), x=>x.segment) : Array.from(paragraph);
    let line='';
    for (const letter of letters) {
      if (line && measure(line+letter)>width) {output.push(line);line='';}
      line+=letter;
    }
    output.push(line);
  }
  return output;
}

export function layout(snapshot,measure) {
  const pages=[[]],gap=24,padding=30,inner=RIGHT-LEFT-padding*2;
  const textRows=(text,width,size=28,bold=false,color='#222222',height=43)=>wrap(text,width,t=>measure(t,size,bold)).map(text=>({text,size,bold,color,height}));
  const header=textRows(snapshot.activity.title,RIGHT-LEFT,44,true,'#222222',58);
  const top=Math.max(250,98+header.length*58+90);
  const entries=snapshot.works.map((work,index)=>({work,rank:String(index+1).padStart(2,'0'),
    title:textRows(work.title,inner-180,34,true,'#222222',47),
    author:textRows(work.author||'作者未填写',inner-180,25,false,'#717171',36)}));
  // All cards in one export share a height; reason counts never change it.
  const height=Math.max(260,...entries.map(entry=>padding*2+entry.title.length*47+entry.author.length*36+50));
  if(top+height>BOTTOM) throw new Error('书名或活动名称过长，无法排版，请调整后重试');
  let y=top;
  if(!entries.length) pages[0].push({text:'本期暂无推荐作品',x:LEFT,y,size:30,color:'#717171',height:50});
  for(const {work,rank,title,author} of entries) {
    if(y+height>BOTTOM) {pages.push([]);y=top;}
    const rows=pages.at(-1),base=y+padding;
    rows.push({type:'card',y,height});
    rows.push({type:'cover',url:work.cover_url,rank,x:LEFT+padding,y:base,height:200});
    let ty=base;
    for(const row of [...title,...author]) {rows.push({...row,x:LEFT+padding+180,y:ty});ty+=row.height;}
    rows.push({text:`推荐 ${work.recommendation_count}   ·   点赞 ${work.like_count}   ·   理由 ${work.reason_count??0}`,
      size:24,color:'#555555',x:LEFT+padding+180,y:ty+16,height:34});
    y+=height+gap;
  }
  if(pages.length>300) throw new Error('图片超过 300 张，暂无法一次导出');
  return {pages,header,snapshot};
}

export function render(canvas,plan,index,images=new Map()) {
  const bottom=Math.max(420,...plan.pages[index].map(row=>row.y+row.height));
  const height=index===plan.pages.length-1?Math.min(HEIGHT,Math.max(700,bottom+104)):HEIGHT;
  canvas.width=WIDTH;canvas.height=height;
  const ctx=canvas.getContext('2d');ctx.fillStyle='#f1f3ed';ctx.fillRect(0,0,WIDTH,height);ctx.textBaseline='top';
  const text=(value,x,y,size=24,color='#717171',bold=false)=>{ctx.font=`${bold?650:400} ${size}px ${FONT}`;ctx.fillStyle=color;ctx.fillText(value,x,y);};
  text('ヨイツ図書館  /  主题推荐',LEFT,46,22,'#717171');
  let hy=94;for(const row of plan.header){text(row.text,LEFT,hy,row.size,row.color,true);hy+=row.height;}
  text(`${plan.snapshot.activity.start_label} — ${plan.snapshot.activity.end_label}`,LEFT,hy+10,22);
  text(`${plan.snapshot.total} 部作品  ·  ${plan.snapshot.recommendations} 次推荐`,LEFT,hy+47,24,'#555555');
  for(const row of plan.pages[index]) {
    if(row.type==='card') {
      ctx.fillStyle='#fffefa';ctx.beginPath();ctx.roundRect(LEFT,row.y,RIGHT-LEFT,row.height,14);ctx.fill();
    } else if(row.type==='cover') {
      ctx.fillStyle='#e7ecdf';ctx.fillRect(row.x,row.y,140,200);
      const img=images.get(row.url);
      if(img) {const scale=Math.min(140/img.naturalWidth,200/img.naturalHeight),w=img.naturalWidth*scale,h=img.naturalHeight*scale;ctx.drawImage(img,row.x+(140-w)/2,row.y+(200-h)/2,w,h);}
      else {text('暂无',row.x+45,row.y+76,24);text('封面',row.x+45,row.y+110,24);}
      ctx.fillStyle='#222222';ctx.fillRect(row.x-8,row.y-8,46,34);text(row.rank,row.x+1,row.y-3,22,'#ffffff',true);
    } else if(row.text) text(row.text,row.x??LEFT,row.y,row.size,row.color,row.bold);
  }
  text('推荐理由请到网站的活动书单查看',LEFT,height-78,21);
  text(`快照 ${plan.snapshot.generated_label} · 按推荐数排序`,LEFT,height-46,20);
  ctx.textAlign='right';text(`${index+1} / ${plan.pages.length}`,RIGHT,height-50,22);ctx.textAlign='left';
}

export function crc32(data) {
  let crc=0xffffffff;
  for (const byte of data) {crc^=byte;for(let n=0;n<8;n++) crc=(crc>>>1)^((crc&1) ? 0xedb88320 : 0);}
  return (crc^0xffffffff)>>>0;
}

// PNG is already compressed; a standard stored ZIP needs no extra dependencies.
export function zipParts(files) {
  const chunks=[],directory=[];let offset=0,centralSize=0;
  for (const file of files) {
    const name=new TextEncoder().encode(file.name),crc=crc32(file.bytes);
    const local=new Uint8Array(30+name.length),l=new DataView(local.buffer);
    l.setUint32(0,0x04034b50,true);l.setUint16(4,20,true);l.setUint16(6,0x800,true);
    l.setUint16(12,33,true);l.setUint32(14,crc,true);l.setUint32(18,file.bytes.length,true);l.setUint32(22,file.bytes.length,true);l.setUint16(26,name.length,true);local.set(name,30);
    const central=new Uint8Array(46+name.length),c=new DataView(central.buffer);
    c.setUint32(0,0x02014b50,true);c.setUint16(4,20,true);c.setUint16(6,20,true);c.setUint16(8,0x800,true);c.setUint16(14,33,true);
    c.setUint32(16,crc,true);c.setUint32(20,file.bytes.length,true);c.setUint32(24,file.bytes.length,true);c.setUint16(28,name.length,true);c.setUint32(42,offset,true);central.set(name,46);
    chunks.push(local,file.bytes);directory.push(central);offset+=local.length+file.bytes.length;centralSize+=central.length;
  }
  const end=new Uint8Array(22),e=new DataView(end.buffer);e.setUint32(0,0x06054b50,true);e.setUint16(8,files.length,true);e.setUint16(10,files.length,true);e.setUint32(12,centralSize,true);e.setUint32(16,offset,true);
  return [...chunks,...directory,end];
}
