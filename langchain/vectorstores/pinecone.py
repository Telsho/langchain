"""Wrapper around Pinecone vector database."""
from __future__ import annotations

import logging
import uuid
from typing import Any, Callable, Iterable, List, Optional, Tuple, Dict
from collections import defaultdict

import numpy as np

from langchain.docstore.document import Document
from langchain.embeddings.base import Embeddings
from langchain.vectorstores.base import VectorStore
from langchain.vectorstores.utils import maximal_marginal_relevance

logger = logging.getLogger(__name__)


class Pinecone(VectorStore):
    """Wrapper around Pinecone vector database.

    To use, you should have the ``pinecone-client`` python package installed.

    Example:
        .. code-block:: python

            from langchain.vectorstores import Pinecone
            from langchain.embeddings.openai import OpenAIEmbeddings
            import pinecone

            # The environment should be the one specified next to the API key
            # in your Pinecone console
            pinecone.init(api_key="***", environment="...")
            index = pinecone.Index("langchain-demo")
            embeddings = OpenAIEmbeddings()
            vectorstore = Pinecone(index, embeddings.embed_query, "text")
    """

    def __init__(
        self,
        index: Any,
        embedding_function: Callable,
        text_key: str,
        namespace: Optional[str] = None,
    ):
        """Initialize with Pinecone client."""
        try:
            import pinecone
        except ImportError:
            raise ValueError(
                "Could not import pinecone python package. "
                "Please install it with `pip install pinecone-client`."
            )
        if not isinstance(index, pinecone.index.Index):
            raise ValueError(
                f"client should be an instance of pinecone.index.Index, "
                f"got {type(index)}"
            )
        self._index = index
        self._embedding_function = embedding_function
        self._text_key = text_key
        self._namespace = namespace

    def add_texts(
        self,
        texts: Iterable[str],
        metadatas: Optional[List[dict]] = None,
        ids: Optional[List[str]] = None,
        namespace: Optional[str] = None,
        batch_size: int = 32,
        **kwargs: Any,
    ) -> List[str]:
        """Run more texts through the embeddings and add to the vectorstore.

        Args:
            texts: Iterable of strings to add to the vectorstore.
            metadatas: Optional list of metadatas associated with the texts.
            ids: Optional list of ids to associate with the texts.
            namespace: Optional pinecone namespace to add the texts to.

        Returns:
            List of ids from adding the texts into the vectorstore.

        """
        if namespace is None:
            namespace = self._namespace
        # Embed and create the documents
        docs = []
        ids = ids or [str(uuid.uuid4()) for _ in texts]
        for i, text in enumerate(texts):
            embedding = self._embedding_function(text)
            metadata = metadatas[i] if metadatas else {}
            metadata[self._text_key] = text
            docs.append((ids[i], embedding, metadata))
        # upsert to Pinecone
        self._index.upsert(vectors=docs, namespace=namespace, batch_size=batch_size)
        return ids

    def search_documents_by_metadata(
        self,
        k: int = 4,
        metadata: Optional[List[dict]] = None,
        include_metadata: bool = True,
        include_values: bool = True,
        filters: Optional[dict] = None,
        index_dimensions: int = 1536,
        namespace: Optional[str] = None
    ) -> List[Document]:
        """Search documents based on metadata. You can provide a list of metadata's dictionnary to find and you can also
        provide your own filters based on documentation (https://docs.pinecone.io/docs/metadata-filtering) 

        Args:
            k: Number of Documents to return. Defaults to 4.
            metadata: List of dictionnary to filter. Default to None.
            include_metadata: include metadata from the returned documents. Defaults to True
            inclue_values: include vector values from the returned documents. Defaults to False.
            filters: provide your own filters based on documentation (https://docs.pinecone.io/docs/metadata-filtering). Defaults to None
            index_dimensions: Dimensions of your index. Defaults to 1536.
            namespace: Search in a particular namespace. Default to None.
        Returns:
            List of Documents selected by the metadata and filters provided.
        """

        if namespace is None:
            namespace = self._namespace
        
        # Prepare your query
        query : Dict = {"$and": []}

        if filters is not None:
            # Add pre-existing filters to the query
            query["$and"].append({"$or": filters})

        if metadata is not None:
            #We do this to avoid aving the same values in the arrays
            values_by_key = defaultdict(set)

            # Loop through your data and gather values by key
            for entry in metadata:
                for key, value in entry.items():
                    if(type(value) == list):
                        values_by_key[key].update(value)
                    else: 
                        values_by_key[key].add(value)

            # Add conditions to the query using the gathered values
            for key, values in values_by_key.items():
                query["$and"].append({key: {"$in": list(values)}})

        vector = [0] * index_dimensions

        return self._index.query(vector=vector, filter=query, top_k=k, include_metadata=include_metadata, include_values=include_values, namespace=namespace)["matches"]
    

    def similarity_search_with_score(
        self,
        query: str,
        k: int = 4,
        filter: Optional[dict] = None,
        namespace: Optional[str] = None,
    ) -> List[Tuple[Document, float]]:
        """Return pinecone documents most similar to query, along with scores.

        Args:
            query: Text to look up documents similar to.
            k: Number of Documents to return. Defaults to 4.
            filter: Dictionary of argument(s) to filter on metadata
            namespace: Namespace to search in. Default will search in '' namespace.

        Returns:
            List of Documents most similar to the query and score for each
        """
        if namespace is None:
            namespace = self._namespace
        query_obj = self._embedding_function(query)
        docs = []
        results = self._index.query(
            [query_obj],
            top_k=k,
            include_metadata=True,
            namespace=namespace,
            filter=filter,
        )
        for res in results["matches"]:
            metadata = res["metadata"]
            if self._text_key in metadata:
                text = metadata.pop(self._text_key)
                score = res["score"]
                docs.append((Document(page_content=text, metadata=metadata), score))
            else:
                logger.warning(
                    f"Found document with no `{self._text_key}` key. Skipping."
                )
        return docs

    def similarity_search(
        self,
        query: str,
        k: int = 4,
        filter: Optional[dict] = None,
        namespace: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Document]:
        """Return pinecone documents most similar to query.

        Args:
            query: Text to look up documents similar to.
            k: Number of Documents to return. Defaults to 4.
            filter: Dictionary of argument(s) to filter on metadata
            namespace: Namespace to search in. Default will search in '' namespace.

        Returns:
            List of Documents most similar to the query and score for each
        """
        docs_and_scores = self.similarity_search_with_score(
            query, k=k, filter=filter, namespace=namespace, **kwargs
        )
        return [doc for doc, _ in docs_and_scores]

    def _similarity_search_with_relevance_scores(
        self,
        query: str,
        k: int = 4,
        **kwargs: Any,
    ) -> List[Tuple[Document, float]]:
        kwargs.pop("score_threshold", None)
        return self.similarity_search_with_score(query, k, **kwargs)

    def max_marginal_relevance_search_by_vector(
        self,
        embedding: List[float],
        k: int = 4,
        fetch_k: int = 20,
        lambda_mult: float = 0.5,
        filter: Optional[dict] = None,
        namespace: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Document]:
        """Return docs selected using the maximal marginal relevance.

        Maximal marginal relevance optimizes for similarity to query AND diversity
        among selected documents.

        Args:
            embedding: Embedding to look up documents similar to.
            k: Number of Documents to return. Defaults to 4.
            fetch_k: Number of Documents to fetch to pass to MMR algorithm.
            lambda_mult: Number between 0 and 1 that determines the degree
                        of diversity among the results with 0 corresponding
                        to maximum diversity and 1 to minimum diversity.
                        Defaults to 0.5.
        Returns:
            List of Documents selected by maximal marginal relevance.
        """
        if namespace is None:
            namespace = self._namespace
        results = self._index.query(
            [embedding],
            top_k=fetch_k,
            include_values=True,
            include_metadata=True,
            namespace=namespace,
            filter=filter,
        )
        mmr_selected = maximal_marginal_relevance(
            np.array([embedding], dtype=np.float32),
            [item["values"] for item in results["matches"]],
            k=k,
            lambda_mult=lambda_mult,
        )
        selected = [results["matches"][i]["metadata"] for i in mmr_selected]
        return [
            Document(page_content=metadata.pop((self._text_key)), metadata=metadata)
            for metadata in selected
        ]

    def max_marginal_relevance_search(
        self,
        query: str,
        k: int = 4,
        fetch_k: int = 20,
        lambda_mult: float = 0.5,
        filter: Optional[dict] = None,
        namespace: Optional[str] = None,
        **kwargs: Any,
    ) -> List[Document]:
        """Return docs selected using the maximal marginal relevance.

        Maximal marginal relevance optimizes for similarity to query AND diversity
        among selected documents.

        Args:
            query: Text to look up documents similar to.
            k: Number of Documents to return. Defaults to 4.
            fetch_k: Number of Documents to fetch to pass to MMR algorithm.
            lambda_mult: Number between 0 and 1 that determines the degree
                        of diversity among the results with 0 corresponding
                        to maximum diversity and 1 to minimum diversity.
                        Defaults to 0.5.
        Returns:
            List of Documents selected by maximal marginal relevance.
        """
        embedding = self._embedding_function(query)
        return self.max_marginal_relevance_search_by_vector(
            embedding, k, fetch_k, lambda_mult, filter, namespace
        )

    @classmethod
    def from_texts(
        cls,
        texts: List[str],
        embedding: Embeddings,
        metadatas: Optional[List[dict]] = None,
        ids: Optional[List[str]] = None,
        batch_size: int = 32,
        text_key: str = "text",
        index_name: Optional[str] = None,
        namespace: Optional[str] = None,
        **kwargs: Any,
    ) -> Pinecone:
        """Construct Pinecone wrapper from raw documents.

        This is a user friendly interface that:
            1. Embeds documents.
            2. Adds the documents to a provided Pinecone index

        This is intended to be a quick way to get started.

        Example:
            .. code-block:: python

                from langchain import Pinecone
                from langchain.embeddings import OpenAIEmbeddings
                import pinecone

                # The environment should be the one specified next to the API key
                # in your Pinecone console
                pinecone.init(api_key="***", environment="...")
                embeddings = OpenAIEmbeddings()
                pinecone = Pinecone.from_texts(
                    texts,
                    embeddings,
                    index_name="langchain-demo"
                )
        """
        try:
            import pinecone
        except ImportError:
            raise ValueError(
                "Could not import pinecone python package. "
                "Please install it with `pip install pinecone-client`."
            )

        indexes = pinecone.list_indexes()  # checks if provided index exists

        if index_name in indexes:
            index = pinecone.Index(index_name)
        elif len(indexes) == 0:
            raise ValueError(
                "No active indexes found in your Pinecone project, "
                "are you sure you're using the right API key and environment?"
            )
        else:
            raise ValueError(
                f"Index '{index_name}' not found in your Pinecone project. "
                f"Did you mean one of the following indexes: {', '.join(indexes)}"
            )

        for i in range(0, len(texts), batch_size):
            # set end position of batch
            i_end = min(i + batch_size, len(texts))
            # get batch of texts and ids
            lines_batch = texts[i:i_end]
            # create ids if not provided
            if ids:
                ids_batch = ids[i:i_end]
            else:
                ids_batch = [str(uuid.uuid4()) for n in range(i, i_end)]
            # create embeddings
            embeds = embedding.embed_documents(lines_batch)
            # prep metadata and upsert batch
            if metadatas:
                metadata = metadatas[i:i_end]
            else:
                metadata = [{} for _ in range(i, i_end)]
            for j, line in enumerate(lines_batch):
                metadata[j][text_key] = line
            to_upsert = zip(ids_batch, embeds, metadata)

            # upsert to Pinecone
            index.upsert(vectors=list(to_upsert), namespace=namespace)
        return cls(index, embedding.embed_query, text_key, namespace)

    @classmethod
    def from_existing_index(
        cls,
        index_name: str,
        embedding: Embeddings,
        text_key: str = "text",
        namespace: Optional[str] = None,
    ) -> Pinecone:
        """Load pinecone vectorstore from index name."""
        try:
            import pinecone
        except ImportError:
            raise ValueError(
                "Could not import pinecone python package. "
                "Please install it with `pip install pinecone-client`."
            )

        return cls(
            pinecone.Index(index_name), embedding.embed_query, text_key, namespace
        )

    def delete(
        self,
        ids: Optional[List[str]] = None,
        delete_all: Optional[bool] = None,
        namespace: Optional[str] = None,
        filter: Optional[dict] = None,
        **kwargs: Any,
    ) -> None:
        """Delete by vector IDs or filter.
        Args:
            ids: List of ids to delete.
            filter: Dictionary of conditions to filter vectors to delete.
        """

        if namespace is None:
            namespace = self._namespace

        if delete_all:
            self._index.delete(delete_all=True, namespace=namespace, **kwargs)
        elif ids is not None:
            chunk_size = 1000
            for i in range(0, len(ids), chunk_size):
                chunk = ids[i : i + chunk_size]
                self._index.delete(ids=chunk, namespace=namespace, **kwargs)
        elif filter is not None:
            self._index.delete(filter=filter, namespace=namespace, **kwargs)
        else:
            raise ValueError("Either ids, delete_all, or filter must be provided.")

        return None
