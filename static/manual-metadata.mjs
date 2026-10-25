import {normalize} from './text.mjs';
import './tag-autocomplete.mjs?v=20261025-keyboard-1';
export function readMetadata(root = document) {
  let author;
  try { author = normalize(root.querySelector('#manual-author').value, 'title'); }
  catch { throw Error('作者最多 20 字，请检查输入内容'); }
  if (Array.from(author).length > 20) throw Error('作者最多 20 字');
  let description;
  try { description = normalize(root.querySelector('#manual-description').value, 'description'); }
  catch { throw Error('简介最多 2000 字，请检查输入内容'); }
  if (Array.from(description).length > 2000) throw Error('简介最多 2000 字');
  const tags = root.querySelector('#manual-tags').value.split(/[,，\n]/u).map(value => value.trim()).filter(Boolean);
  if (tags.length > 20 || tags.some(value => Array.from(value).length > 30 || /[\u0000-\u001f]/u.test(value))) {
    throw Error('标签最多 20 个，每个限 1—30 字');
  }
  return {author, description, tags};
}
