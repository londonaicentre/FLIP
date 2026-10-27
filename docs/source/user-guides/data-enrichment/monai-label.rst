.. _monai-label-labelling:

#######################################
AI-assisted labelling with MONAI Label
#######################################

Supervised learning needs a label for every image. What that label is depends on the task:

- **Prognosis and classification**: the label is usually already recorded as clinical data, such as
  a COVID test result for a chest X-ray or a diagnosis code. It reaches your app through the cohort
  query, with no enrichment (see :ref:`enrichment-do-i-need-it`).
- **Detection and segmentation**: the label is drawn *on the image* (a box, a contour, a mask), so it
  does not exist until someone draws it. Drawing every slice by hand is the slow part of building a
  dataset.

`MONAI Label <https://monai.io/label.html>`_ speeds that up. It runs at the Trust, next to its
:term:`XNAT`, and adds an AI-assisted labelling panel to the XNAT OHIF viewer: pre-trained models
propose a mask for the whole scan, and interactive models refine one from a few clicks. Images never
leave the Trust: the viewer, the model and the saved labels all stay inside it.

.. note::

   MONAI Label is optional and needs a GPU, so it is switched on per Trust. If the viewer has no
   MONAI Label option, ask the Trust's administrator; the operator guide is the
   `trust README <https://github.com/londonaicentre/FLIP/blob/develop/trust/README.md#monai-label-optional>`_.

******************************
Switch it on in your browser
******************************

Once per browser: open any scan in the OHIF viewer, then *Options → Preferences → Experimental
Features*, tick **MONAILabel Tools** and **Save**. The setting lives in your browser, so a new
browser or a cleared cache needs it again.

.. figure:: ../../assets/generated/gifs/monailabel/monailabel-enable.gif
   :width: 100%
   :align: center

   Switching on MONAI Label in the XNAT OHIF viewer.

*********************************************
Label a scan from nothing with a few clicks
*********************************************

The walkthrough below segments a spleen with **SAM** (Segment Anything), an interactive model that
follows your clicks:

1. In the **Masks** panel, add a **Mask ROI** and name it after the structure (click its label).
2. Open *Mask ▾ → MONAILabel*. The panel shows the Trust's MONAI Label server.
3. Set **Tool type** to *DeepGrow* and the model to **sam_2d**.
4. Pick *Mask ▾ → MONAILabel* **again**. Until you do, clicks on the image do nothing (a known
   viewer defect, see :ref:`monai-label-quirks`).
5. Go to a slice through the structure and **click on it**: the model proposes a mask.
6. Where the mask spills over, **Ctrl+click** to add a negative point; where it misses, click again
   to add a positive one. Each click re-runs the model.

.. figure:: ../../assets/generated/gifs/monailabel/monailabel-sam-clicks.gif
   :width: 100%
   :align: center

   An empty spleen segment, a positive click on the spleen, then a negative Ctrl+click to tidy the
   edge: Dice 0.957 against the expert's label after two clicks.

``sam_2d`` labels the slice you click on. For a whole volume in one go, set **Tool type** to
*Segmentation* and **Run** a pre-trained model, such as ``segmentation_spleen`` or the multi-organ
``deepedit_seg``, then correct its result. Which models are offered is the Trust's choice.

*******************
Save it to XNAT
*******************

**Export** the mask collection from the Masks panel. It is stored on the session as an **ROI
collection** holding a DICOM-SEG. Within about a minute, XNAT's ``export_mask`` command converts it
to NIfTI, resampled onto the scan's own grid, and adds the ``.nii.gz`` beside the DICOM-SEG in the
same ROI collection. *Manage Files* on the ROI collection shows both.

.. figure:: ../../assets/generated/gifs/monailabel/monailabel-export.gif
   :width: 100%
   :align: center

   Exporting the mask: the ROI collection's SEG resource holds the DICOM-SEG and its NIfTI.

***********************************
Use the masks in your federated app
***********************************

Masks made this way live in **ROI collections**, not in the scan's ``NIFTI`` resource, so they follow
a different route from the ``label_*`` files described in :ref:`enrichment-contract`. Request them
with the segmentation resource type:

.. code-block:: python

   from flip.constants import ResourceType

   masks_path = flip_client.get_by_accession_number(
       project_id, accession_id, resource_type=[ResourceType.SEGMENTATION]
   )
   masks = sorted(masks_path.rglob("*.nii.gz"))  # one NIfTI per ROI collection on the session

You receive **every** ROI collection on the session, each with its DICOM-SEG (``.dcm``) and its NIfTI
(``.nii.gz``), so decide in your app which one to train on (for example the most recent, or one
agreed by name), and skip sessions that have none. The NIfTI is already on the scan's grid. Apps
written for the ``label_*`` convention, such as the spleen tutorials, need their label loader adapted
to this route.

.. _monai-label-quirks:

***************************
Known viewer quirks
***************************

These are defects in the XNAT OHIF viewer, not in your labels. The server returns valid masks
throughout.

- **Clicks do nothing** after the MONAI Label panel connects: pick *Mask ▾ → MONAILabel* again.
- **"Empty mask was returned by the model run"** from an interactive model while others work: the
  tab's state has gone stale, so reload it.
- **Interactive results always land in the first segment**, whichever segment is selected. Label
  one structure per collection, or rename afterwards.
