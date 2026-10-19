export const whitespace = '\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff';
export const limits = {reason: 200, title: 300, alias: 300, activity_title: 100, theme: 500, description: 10000};
export function normalize(value, field = 'reason', required = false) {
  if (typeof value !== 'string') throw Error('Invalid Unicode');
  // Iterate code points so valid surrogate pairs pass on older JS runtimes too.
  for (const character of value) {
    const point = character.codePointAt(0);
    if (point >= 0xD800 && point <= 0xDFFF) throw Error('Invalid Unicode');
  }
  let result = value.replace(/\r\n?/g, '\n');
  let start = 0, end = result.length;
  while (start < end && whitespace.includes(result[start])) start++;
  while (end > start && whitespace.includes(result[end - 1])) end--;
  result = result.slice(start, end);
  if ((required && !result) || Array.from(result).length > limits[field]) throw Error('Invalid length');
  return result;
}
