######################
Working with FLIP apps
######################

A FLIP app is the user-supplied federated-learning code that runs across the
Central Hub and each participating Trust. The guides in this section walk
through how to adapt or build apps so they can be uploaded to FLIP and
orchestrated by the FL framework (Flower or NVIDIA FLARE).

**Start with** :doc:`working-with-flip-apps/choosing-an-fl-framework`: your app is written for one framework, and
the FLIP deployment you train on decides which. Then follow the matching guide to adapt your app, and, once a model
is trained, :doc:`working-with-flip-apps/wrap-model-for-deployment` to wrap it for clinical deployment.

One rule applies to every app before anything framework-specific: **an app is offline**.
Whatever it needs at run time — pretrained weights, checkpoints, an auxiliary network for a
loss — must be one of the files uploaded with it, never something the code fetches when the
model is built (``pretrained=True``, ``torch.hub.load``, ``from_pretrained("org/model")``,
``load_state_dict_from_url``). On a platform-managed estate the FL server has no route to the
internet, and neither does a Trust's training host behind an NHS firewall, so an app cannot
assume one anywhere: such a call hangs the job there; and a file fetched after the upload was
inspected is not the file that was inspected. The *Model files* section of the
:ref:`user guide <model-files>` spells out the failure modes; the latent-diffusion
tutorial shows how to ship a network a library would otherwise download.

.. toctree::
   :maxdepth: 2

   working-with-flip-apps/choosing-an-fl-framework
   working-with-flip-apps/flip-utils-package
   working-with-flip-apps/create-flip-app-from-flower
   working-with-flip-apps/create-flip-app-from-flare
   working-with-flip-apps/wrap-model-for-deployment
   working-with-flip-apps/map-toolchain-reference
