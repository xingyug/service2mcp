const { ApolloServer } = require('@apollo/server');
const { expressMiddleware } = require('@apollo/server/express4');
const express = require('express');
const cors = require('cors');
const http = require('http');

const typeDefs = `#graphql
  enum Category { BOOKS GAMES }
  enum AdjustmentReason { SALE RESTOCK }
  input PriceRangeInput { min: Float, max: Float }
  input ProductFilter { priceRange: PriceRangeInput, tags: [String] }
  type Product { id: ID!, name: String }
  type InventoryResult { ok: Boolean }
  type Query {
    searchProducts(term: String!, category: Category, filter: ProductFilter, limit: Int = 10): [Product!]!
  }
  type Mutation {
    adjustInventory(sku: ID!, delta: Int!, reason: AdjustmentReason): InventoryResult
  }
`;

const resolvers = {
  Query: {
    searchProducts: (_, { term, limit }) => {
      const products = [
        { id: '1', name: 'The Great Gatsby' },
        { id: '2', name: 'Chess Master 3000' },
        { id: '3', name: 'Python Cookbook' },
        { id: '4', name: 'Monopoly Classic' },
        { id: '5', name: 'Clean Code' },
      ];
      return products
        .filter(p => p.name.toLowerCase().includes((term || '').toLowerCase()))
        .slice(0, limit || 10);
    },
  },
  Mutation: {
    adjustInventory: (_, { sku, delta, reason }) => ({ ok: true }),
  },
};

async function start() {
  const app = express();
  const httpServer = http.createServer(app);

  const server = new ApolloServer({ typeDefs, resolvers, introspection: true });
  await server.start();

  app.get('/healthz', (_req, res) => {
    res.json({ status: 'ok' });
  });

  app.use('/graphql', cors(), express.json(), expressMiddleware(server));
  // Also mount at root so introspection works at http://host:4000/
  app.use('/', cors(), express.json(), expressMiddleware(server));

  httpServer.listen(4000, '0.0.0.0', () => {
    console.log('GraphQL server ready at http://0.0.0.0:4000/graphql');
    console.log('Health check available at http://0.0.0.0:4000/healthz');
  });
}

start();
