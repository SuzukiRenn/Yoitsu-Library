// A failed external image must not look like an image still being downloaded.
const enhancedCovers = new WeakSet();
export function initializeCovers(scope = document) {
for (const image of scope.querySelectorAll('[data-cover-image]')) {
  if (enhancedCovers.has(image)) continue;
  enhancedCovers.add(image);
  const failed = () => {
    image.hidden = true;
    const fallback = image.parentElement.querySelector('.cover-fallback');
    if (fallback) fallback.hidden = false;
  };
  image.addEventListener('error', failed, {once: true});
  if (image.complete && !image.naturalWidth) failed();
}

}
if (typeof document !== 'undefined') initializeCovers();
