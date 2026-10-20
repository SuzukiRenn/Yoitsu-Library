// A failed external image must not look like an image still being downloaded.
for (const image of document.querySelectorAll('[data-cover-image]')) {
  const failed = () => {
    image.hidden = true;
    const fallback = image.parentElement.querySelector('.cover-fallback');
    if (fallback) fallback.hidden = false;
  };
  image.addEventListener('error', failed, {once: true});
  if (image.complete && !image.naturalWidth) failed();
}
