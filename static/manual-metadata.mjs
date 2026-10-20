import {normalize} from './text.mjs';
export function readMetadata(root = document) {
  let description;
  try { description = normalize(root.querySelector('#manual-description').value, 'description'); }
  catch { throw Error('简介最多 10000 字，请检查输入内容'); }
  const tags = root.querySelector('#manual-tags').value.split(/[,，\n]/u).map(value => value.trim()).filter(Boolean);
  if (tags.length > 20 || tags.some(value => Array.from(value).length > 30 || /[\u0000-\u001f]/u.test(value))) {
    throw Error('标签最多 20 个，每个限 1—30 字');
  }
  return {description, tags};
}
