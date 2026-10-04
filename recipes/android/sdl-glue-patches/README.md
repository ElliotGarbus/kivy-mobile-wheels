# SDL Java glue patches

`sdl2.sh` copies SDL's Java glue out of the pinned tarball into
`sdl-glue/<version>/`, then applies every `<version>/*.patch` here, in name
order, with `patch -p1` from inside `sdl-glue/<version>/`. The committed glue is
therefore always the tarball's files plus these patches, and the glue check in
`build-android.yml` still fails if anything else changed.

A patch here may only change Java. Anything that changes a native method's
signature or the version handshake would break the pairing with `libSDL2.so`
described in the repo README.

## `2.32.10/keyboard-input-type.patch`

Kivy 2.3.1 sets the soft keyboard's type (`TextInput.input_type`) by calling
`changeKeyboard(inputType)` on the app's `PythonActivity`, which stores it in
`SDLActivity.keyboardInputType` and restarts input. Stock SDL hard-codes the
type in `DummyEdit.onCreateInputConnection`, so the patch:

- adds `public static int keyboardInputType` to `SDLActivity`, defaulting to
  stock SDL's own value (`TYPE_CLASS_TEXT | TYPE_TEXT_FLAG_MULTI_LINE`), so an
  app that never calls `changeKeyboard` behaves exactly as before;
- makes `DummyEdit.onCreateInputConnection` read that field.

The two hunks are the keyboard part of python-for-android's
`bootstraps/sdl2/build/src/patches/SDLActivity.java.patch`; its other hunks
are not needed here. The default differs from python-for-android's
(`TYPE_TEXT_VARIATION_VISIBLE_PASSWORD`) because Kivy passes the type it wants
on every focus, and the stock default is the safer one before that.

SDL3's glue (`sdl-glue-sdl3/`) is not patched: Kivy 3 does not call
`changeKeyboard`.
