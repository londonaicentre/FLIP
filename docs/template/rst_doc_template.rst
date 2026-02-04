###########
Page Title
###########

This is a placeholder preamble before delving into the page main contents.

.. warning::
    This is a warning block to indicate **Prerequisites**, if any, or note any known issues, caveats, etc.

.. note::
    You can also include a note block to indicate lower priority warning or notes, such as good-to-know pieces of information.

********
Chapter
********

We recommend organising content into high-level headers (this formatting is referred to as `chapters` in `reStructuredText`) and then further dividing, if applicable, into sections, sub-sections, and so on.

Section
=============

Sub-section
------------

You can also include:

* Tables

    .. list-table::
       :widths: 10 10 10
       :header-rows: 1

       * - Column header 1
         - Column header 2
         - Column header 3
       * - Column header 1 row value
         - Column header 2 row value
         - Column header 3 row value

* Code snippets

    .. code-block:: python

        # This is a Python code block

* Images, figures, GIFs, etc.

    .. figure:: ../assets/figure.jpeg
       :width: 300

       This is the figure's caption.

* References

    You can add references to words or phrases by adding:
    1. A BibTeX entry to ``docs/source/references.bib``
    2. ``:footcite:p:`YYYY:referenceshorthand``` after the word to which you would like to add the reference superscript
    3. A ``References`` section at the bottom of the page and reference the bibliography e.g,

       References
       ==========

       .. footbibliography::
