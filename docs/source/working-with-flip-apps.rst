######################
Working with FLIP apps
######################

A FLIP app is the user-supplied federated-learning code that runs across the
Central Hub and each participating Trust. The guides in this section walk
through how to adapt or build apps so they can be uploaded to FLIP and
orchestrated by the FL framework (Flower or NVIDIA FLARE).

One rule applies to every app before anything framework-specific: **an app is offline**.
Whatever it needs at run time — pretrained weights, checkpoints, an auxiliary network for a
loss — must be one of the files uploaded with it, never something the code fetches when the
model is built (``pretrained=True``, ``torch.hub.load``, ``from_pretrained("org/model")``,
``load_state_dict_from_url``). The FL server and the Trusts' training hosts have no route to the
internet, so such a call hangs the job; and a file fetched after review is not the file the
reviewer saw. The *Model Files* section of the :doc:`user guide <user-guides/user-common>`
spells out the failure modes; the latent-diffusion tutorial shows how to ship a network a
library would otherwise download.

.. toctree::
   :maxdepth: 2

   working-with-flip-apps/flip-utils-package
   working-with-flip-apps/create-flip-app-from-flower
   working-with-flip-apps/create-flip-app-from-flare
   working-with-flip-apps/package-model-as-map
