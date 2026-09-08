package org.mkwpc.quest;

import org.libsdl.app.SDLActivity;

/**
 * SDL3 does the whole Android lifecycle; this only says which native libraries
 * to load. SDL is linked statically into the product, so there is exactly one:
 * the product itself, built as libmain.so.
 */
public class MkwActivity extends SDLActivity {
    @Override
    protected String[] getLibraries() {
        return new String[] { "main" };
    }
}
